import os
import json
import logging
import httpx
from fastapi import FastAPI, Request, Query, Response, BackgroundTasks

# लॉगिंग सेटअप
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("InstagramBot")

app = FastAPI()

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_secret_token_123")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
GRAPH_API_VERSION = "v19.0"

def load_rules():
    """rules.json से कॉन्फ़िगरेशन लोड करता है"""
    if os.path.exists("rules.json"):
        try:
            with open("rules.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"rules.json पढ़ने में त्रुटि: {e}")
    return {}

async def send_text_dm(recipient_id: str, message_text: str):
    """Instagram DM में टेक्स्ट संदेश भेजता है"""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(url, json=payload, headers=headers)
            logger.info(f"Text DM Status: {res.status_code} | Res: {res.text}")
        except Exception as e:
            logger.error(f"Text DM भेजने में नेटवर्क एरर: {e}")

async def send_attachment_dm(recipient_id: str, file_url: str, caption: str = ""):
    """Instagram DM में फाइल (PDF/Image) अटैचमेंट भेजता है"""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "file",
                "payload": {
                    "url": file_url,
                    "is_reusable": True
                }
            }
        }
    }
    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.post(url, json=payload, headers=headers)
            logger.info(f"File DM Status: {res.status_code} | Res: {res.text}")
            if caption:
                await send_text_dm(recipient_id, caption)
        except Exception as e:
            logger.error(f"Attachment DM भेजने में नेटवर्क एरर: {e}")

async def process_comment(sender_id: str, media_id: str, comment_text: str):
    """कमेंट प्रोसेस करके तय करता है कि कौन सा DM भेजना है"""
    rules = load_rules()
    
    # 1. पहले चेक करें क्या इस विशिष्ट रील की ID के लिए नियम बना है
    rule = rules.get(media_id)
    
    # 2. अगर उस रील की ID नहीं मिली, तो DEFAULT नियम लागू करें
    if not rule:
        rule = rules.get("DEFAULT")
        
    if not rule:
        logger.warning(f"रील ID {media_id} के लिए कोई नियम या डिफ़ॉल्ट सेट नहीं मिला।")
        return

    target_keyword = rule.get("keyword", "LINK").strip().lower()
    
    # कीवर्ड मैच चेक (Case-insensitive)
    if target_keyword in comment_text.lower():
        logger.info(f"कीवर्ड '{target_keyword}' मैच हुआ! DM भेजा जा रहा है...")
        
        msg_type = rule.get("type", "text")
        if msg_type == "file":
            file_url = rule.get("url")
            caption = rule.get("message", "")
            if file_url:
                await send_attachment_dm(sender_id, file_url, caption)
        else:
            message_text = rule.get("message", "यहाँ आपका लिंक है!")
            await send_text_dm(sender_id, message_text)

# --- Webhook Routes ---

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token")
):
    """Meta Webhook सेटअप वेरिफिकेशन"""
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)

@app.post("/webhook")
async def handle_events(request: Request, background_tasks: BackgroundTasks):
    """Instagram से आने वाले कमेंट इवेंट्स को तुरंत एक्सेप्ट करके बैकग्राउंड में प्रोसेस करता है"""
    try:
        data = await request.json()
    except Exception:
        return Response(content="Invalid JSON", status_code=400)

    if data.get("object") == "instagram":
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") == "comments":
                    value = change.get("value", {})
                    comment_text = value.get("text", "").strip()
                    sender_id = value.get("from", {}).get("id")
                    media_id = value.get("media", {}).get("id")

                    logger.info(f"नया कमेंट: Media ID: {media_id} | Sender ID: {sender_id} | Comment: {comment_text}")

                    if sender_id and media_id:
                        # बैकग्राउंड टास्क ताकि Meta को तुरंत 200 OK मिल जाए और टाइमआउट न हो
                        background_tasks.add_task(process_comment, sender_id, media_id, comment_text)

    return {"status": "ok"}
          
