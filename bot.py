import os
import time
import openai
import asyncio
import logging
from dotenv import load_dotenv
from flask import Flask, request, Response
from botbuilder.core import BotFrameworkAdapterSettings, BotFrameworkAdapter, TurnContext
from botbuilder.schema import Activity

# Load env vars
load_dotenv()

# Azure Bot credentials
APP_ID = os.getenv("MicrosoftAppId", "")
APP_PASSWORD = os.getenv("MicrosoftAppPassword", "")

# Azure OpenAI config
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

openai.api_type = "azure"
openai.api_version = "2024-05-01-preview"
openai.api_key = AZURE_OPENAI_API_KEY
openai.azure_endpoint = AZURE_OPENAI_ENDPOINT.rstrip("/")

# Flask + Bot Framework
app = Flask(__name__)
adapter_settings = BotFrameworkAdapterSettings(APP_ID, APP_PASSWORD)
adapter = BotFrameworkAdapter(adapter_settings)

# Memory map for OpenAI threads
thread_map = {}

async def handle_message(turn_context: TurnContext):
    # Greeting on bot added
    if turn_context.activity.type == "conversationUpdate":
        for member in turn_context.activity.members_added:
            if member.id == turn_context.activity.recipient.id:
                await turn_context.send_activity("Hello! How can I assist you today?")
        return

    # Ignore non-text events
    if turn_context.activity.type != "message" or not turn_context.activity.text:
        return

    user_id = turn_context.activity.from_property.id
    user_input = turn_context.activity.text.strip()

    try:
        await turn_context.send_activity(Activity(type="typing"))

        # Thread handling
        thread_id = thread_map.get(user_id)
        if not thread_id:
            thread = openai.beta.threads.create()
            thread_id = thread.id
            thread_map[user_id] = thread_id

        # Add user message
        openai.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=user_input
        )

        # Run assistant
        run = openai.beta.threads.runs.create(
            assistant_id=ASSISTANT_ID,
            thread_id=thread_id
        )

        # Poll for completion
        while run.status not in ["completed", "failed", "cancelled"]:
            time.sleep(1)
            run = openai.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )

        # Get latest assistant message
        messages = openai.beta.threads.messages.list(thread_id=thread_id)
        assistant_reply = None

        for message in reversed(messages.data):
            if message.role == "assistant":
                assistant_reply = message.content[0].text.value
                break

        if not assistant_reply:
            assistant_reply = "Sorry, I couldn't generate a response."

    except Exception as e:
        logging.error(f"Error: {e}")
        assistant_reply = "Something went wrong."

    await turn_context.send_activity(Activity(
        type="message",
        text=assistant_reply
    ))

@app.route("/api/messages", methods=["POST"])
def messages():
    try:
        if "application/json" not in request.headers.get("Content-Type", ""):
            return Response("Unsupported Media Type", status=415)

        activity = Activity().deserialize(request.json)
        auth_header = request.headers.get("Authorization", "")

        async def process():
            return await adapter.process_activity(activity, auth_header, handle_message)

        asyncio.run(process())
        return Response(status=200)

    except Exception as e:
        logging.error(f"Exception in /api/messages: {e}")
        return Response("Internal Server Error", status=500)

@app.route("/", methods=["GET"])
def root():
    return "Bot is running (single-tenant test)."

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 3978))
    app.run(host="0.0.0.0", port=port)
