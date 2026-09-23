"""
    Neuro-cli
    author@Fedal987
    Powered by HeronStudio
    GitHub: https://github.com/Fedal987/neuro-cli-py
"""

from pathlib import Path
from src.main.ui.i18n import tr

from src.main.api.api_manager import APIRuntime, get_runtime
from src.main.prompt.non_reasoning_prompt import build_prompt, create_agent as create_non_reasoning_agent
from src.main.prompt.reasoning_prompt import SYSTEM_PROMPT as REASONING_PROMPT, create_agent as create_reasoning_agent

class MessageHandler:
    REASONING_LEVELS = ("default", "minimal", "low", "medium", "high", "xhigh", "max")

    def __init__(self, system_prompt: str | None = None, reasoning_enabled: bool | None = None,
                 *, runtime: APIRuntime | None = None):
        self.runtime = runtime if runtime is not None else get_runtime()
        api = self.runtime.config.api
        reasoning = self.runtime.config.reasoning
        self.reasoning_enabled = reasoning.enabled if reasoning_enabled is None else reasoning_enabled
        self.system_prompt = system_prompt if system_prompt is not None else (
            REASONING_PROMPT if self.reasoning_enabled else build_prompt()
        )
        agent_factory = create_reasoning_agent if self.reasoning_enabled else create_non_reasoning_agent
        self.agent = agent_factory(
            system_prompt=self.system_prompt,
            provider=self.runtime.provider,
            workspace=Path.cwd(),
            model=api.model,
            thinking=reasoning.thinking if self.reasoning_enabled else False,
            reasoning_effort=reasoning.effort if self.reasoning_enabled else "",
            auto_approve=reasoning.auto_approve,
            max_steps=reasoning.max_steps,
            temperature=api.temperature,
            command_timeout=reasoning.command_timeout,
        )
        self.history = self.agent.messages
        self.use_stream = api.stream

    def set_model(self, model: str | None = None, effort: str | None = None) -> None:
        if model is not None and (not model.strip() or any(char.isspace() for char in model)):
            raise ValueError(tr("model_name_invalid"))
        if effort is not None and effort not in self.REASONING_LEVELS:
            raise ValueError(tr("model_effort_invalid", levels=", ".join(self.REASONING_LEVELS)))
        if model is not None:
            self.agent.model = model
        if effort is not None:
            self.agent.reasoning_effort = "" if effort == "default" else effort
            self.agent.thinking = True
            self.reasoning_enabled = True

    def add_user_message(self, text: str):
        self.agent.add_user_message(text)

    def add_assistant_message(self, text: str):
        self.agent._append_message({"role": "assistant", "content": text})

    def get_response(self, user_input: str = None) -> str:
        return self.agent.run(user_input)

    def get_response_stream(self, user_input: str = None):
        yield from self.agent.run_stream(user_input)

    def get_response_events(self, user_input: str = None):
        yield from self.agent.run_stream_events(user_input)

    def get_response_stream_internal(self, user_input: str = None):
        yield from self.agent.run_stream(user_input)

    def reset(self):
        self.agent.reset()
        self.history = self.agent.messages

    def set_stream_mode(self, enabled: bool):
        self.use_stream = enabled

    def interrupt(self) -> None:
        self.agent.interrupt()

    def queue_user_message(self, text: str) -> None:
        self.agent.queue_user_message(text)

    def set_interaction_callbacks(self, paused, resumed) -> None:
        self.agent.set_interaction_callbacks(paused, resumed)

    def get_last_user_message(self) -> str | None:
        for msg in reversed(self.history):
            if msg["role"] == "user":
                return msg["content"]
        return None

    def get_last_assistant_message(self) -> str | None:
        for msg in reversed(self.history):
            if msg["role"] == "assistant":
                return msg["content"]
        return None
