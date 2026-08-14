"""Experimento manual de prompt; não é usado pelo tradutor em produção."""

import json
import os

import requests


lines = [
    "It's not like I did it for you or anything.",
    "This is a piece of cake.",
    "また遅刻？信じられない。",
]

system = """Você é um tradutor profissional de legendas de anime para PT-BR.
Produza português brasileiro natural, curto e adequado para leitura em tela.
Nunca traduza expressões idiomáticas literalmente: traduza o sentido e o tom.
Revise cada fala antes de responder para evitar construções estranhas, comida ou
objetos quando a expressão original for figurada. Preserve nomes próprios e o
grau de informalidade. Responda exclusivamente com um array JSON de strings,
na mesma ordem e quantidade da entrada."""

payload = {
    "model": os.environ.get("TRANSLATOR_OLLAMA_MODEL", "qwen3.5:9b"),
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(lines, ensure_ascii=False)},
    ],
    "stream": False,
    "think": False,
    "options": {"temperature": 0.1},
    "keep_alive": "30m",
}

response = requests.post(
    os.environ.get("TRANSLATOR_OLLAMA_URL", "http://192.168.1.5:11434/api/chat"),
    json=payload,
    timeout=240,
)
response.raise_for_status()
print(response.json()["message"]["content"].strip().strip("`"))
