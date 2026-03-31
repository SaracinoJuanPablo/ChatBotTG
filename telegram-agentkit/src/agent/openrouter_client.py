from openai import AsyncOpenAI
from src.config import Config
import logging

logger = logging.getLogger(__name__)

class OpenRouterClient:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=Config.OPENROUTER_KEY,
        )
        self.model = Config.OPENROUTER_MODEL
        
    async def get_response(self, messages, temperature=0.7, max_tokens=2000):
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_headers={"HTTP-Referer": Config.SITE_URL, "X-Title": Config.SITE_NAME}
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Error: {e}")
            return "Error técnico."

openrouter = OpenRouterClient()