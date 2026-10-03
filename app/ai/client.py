import os

from groq import Groq
from dotenv import load_dotenv
load_dotenv()


class LLMClient:
    def __init__(self):
        api_key = os.getenv("GROQ_API_KEY")
        model = os.getenv("GROQ_MODEL")

        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY environment variable is missing"
            )

        if not model:
            raise RuntimeError(
                "GROQ_MODEL environment variable is missing"
            )

        self.client = Groq(
            api_key=api_key
        )

        self.model = model

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={
                "type": "json_object"
            },
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )

        return response.choices[0].message.content