"""
    Neuro-cli
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/neuro-cli-py
"""

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from openai import OpenAI
from pathlib import Path

from src.main.prompt import non_reasoning_prompt
from src.main.ui.terminal_cli import UsageTracker
from src.main.ui.i18n import tr

def _load_config():
    config_path = Path(__file__).parents[3] / "config.toml"
    if not config_path.exists():
        config_path = Path.cwd() / "config.toml"
    if not config_path.exists():
        raise FileNotFoundError(tr("config_not_found"))
    with open(config_path, "r", encoding="utf-8") as f:
        return tomllib.loads(f.read())

_config = _load_config()
BASE_URL = _config["API_MANAGER"]["BASE_URL"]
API_KEY = _config["API_MANAGER"]["API_KEY"]
MODEL = _config["API_MANAGER"]["MODEL"]
STREAM = _config["API_MANAGER"]["STREAM"]
TEMPERATURE = _config["API_MANAGER"]["TEMPREATURE"]
SYSTEM_PROMPT = non_reasoning_prompt.prompt_building

_reasoning_config = _config.get("REASONING", {})
REASONING_ENABLED = _reasoning_config.get("ENABLED", True)
REASONING_THINKING = _reasoning_config.get("THINKING", True)
REASONING_MAX_STEPS = _reasoning_config.get("MAX_STEPS", 12)
REASONING_EFFORT = _reasoning_config.get("EFFORT", "")
REASONING_AUTO_APPROVE = _reasoning_config.get("AUTO_APPROVE", False)
REASONING_COMMAND_TIMEOUT = _reasoning_config.get("COMMAND_TIMEOUT", 60)

USAGE_TRACKER = UsageTracker()

_client = OpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
)

def list_models() -> list[str]:
    """Fetch model IDs from the configured API with a bounded request timeout."""
    page = _client.with_options(timeout=15.0, max_retries=0).models.list()
    return sorted({model.id for model in page.data if isinstance(model.id, str) and model.id.strip()})


def get_completion(messages, stream=False, temperature=None):
    use_stream = stream if stream is not None else STREAM
    try:
        response = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=use_stream,
            temperature=temperature if temperature is not None else TEMPERATURE,
        )
        if use_stream:
            def generator():
                for chunk in response:
                    if chunk.usage is not None:
                        USAGE_TRACKER.record(chunk.usage.model_dump())
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
            return generator()
        else:
            if response.usage is not None:
                USAGE_TRACKER.record(response.usage.model_dump())
            return response.choices[0].message.content
    except Exception as e:
        if use_stream:
            def error_gen():
                yield f"API Error: {str(e)}"
            return error_gen()
        else:
            return f"API Error: {str(e)}"

def get_completion_stream(messages, temperature=None):
    try:
        response = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=True,
            temperature=temperature if temperature is not None else TEMPERATURE,
        )
        for chunk in response:
            if chunk.usage is not None:
                USAGE_TRACKER.record(chunk.usage.model_dump())
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        yield f"API Error: {str(e)}"

if __name__ == "__main__":
    print("System Prompt:", SYSTEM_PROMPT)
    test_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "你好，可以给我做个自我介绍吗?"}
    ]
    reply = get_completion(test_messages)
    print(reply)
