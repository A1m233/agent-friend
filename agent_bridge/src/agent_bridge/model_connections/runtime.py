"""Execution snapshots and owner-only configuration mutation reservations."""

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from threading import RLock
from uuid import uuid4

from llm_providers.connections import ModelDirectory
from llm_providers.connections.codec import decode_connections
from llm_providers.errors import LLMModelUnavailableError
from llm_providers.stream_events import LLMStreamEvent

from llm_providers import LanguageModel, LLMClient, ModelRequest, ModelResponse, ModelRuntimeConfig
from runtime_config import CredentialSource, DesktopSettingsReader, require_secret


class ConnectionRuntime:
    def __init__(
        self, source: CredentialSource, reader: DesktopSettingsReader | None = None
    ) -> None:
        self.source = source
        self.reader = reader or DesktopSettingsReader()
        self._guard = RLock()
        self._active: Counter[str] = Counter()
        self._mutation: str | None = None

    def directory(self) -> ModelDirectory:
        settings = self.reader.read()
        if settings is None:
            # A missing store is a new installation; an existing malformed store is not.
            if self.reader.path.exists():
                raise ValueError("Model settings cannot be read")
            settings = {}
        return ModelDirectory(decode_connections(settings.get("modelConnections")))

    def prepare(self, deleted: list[str], operation_id: str | None = None) -> str:
        with self._guard:
            if self._mutation is not None:
                if operation_id == self._mutation:
                    return self._mutation
                raise ValueError("connection_mutation_pending")
            if any(self._active[f"custom/{identity}"] for identity in deleted):
                raise ValueError("connection_in_use")
            self._mutation = operation_id or str(uuid4())
            return self._mutation

    def finish(self, token: str) -> None:
        with self._guard:
            if self._mutation is None:
                return
            if self._mutation != token:
                raise ValueError("connection_operation_mismatch")
            self.directory()  # Keep admission blocked until the committed snapshot is valid.
            self._mutation = None

    @contextmanager
    def bind(self, config: ModelRuntimeConfig) -> Iterator[LanguageModel]:
        with self._guard:
            if self._mutation is not None:
                raise LLMModelUnavailableError("模型配置正在应用，请稍后重试。")
            directory = self.directory()
            spec = directory.build_spec(
                config,
                api_key=require_secret(self.source, directory.credential_name(config.model_id)),
            )
            identity = spec.provider_id or ""
            self._active[identity] += 1
        try:
            yield LLMClient(spec)
        finally:
            with self._guard:
                self._active[identity] -= 1
                if self._active[identity] == 0:
                    del self._active[identity]

    def model(self, selection: Callable[[], ModelRuntimeConfig]) -> "ConfiguredLanguageModel":
        return ConfiguredLanguageModel(self, selection)


class ConfiguredLanguageModel:
    def __init__(
        self, runtime: ConnectionRuntime, selection: Callable[[], ModelRuntimeConfig]
    ) -> None:
        self.runtime, self.selection = runtime, selection

    def bind_execution(self) -> AbstractContextManager[LanguageModel]:
        return self.runtime.bind(self.selection())

    @property
    def context_window(self) -> int:
        return self.runtime.directory().model(self.selection().model_id).context_window

    @property
    def capabilities(self):  # type: ignore[no-untyped-def]
        from llm_providers import LanguageModelCapabilities

        model = self.runtime.directory().model(self.selection().model_id)
        return LanguageModelCapabilities(
            assistant_message_phase=model.responses.assistant_message_phase
            if model.responses
            else "unavailable"
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        with self.bind_execution() as model:
            return model.generate(request)

    def stream(self, request: ModelRequest) -> Iterator[LLMStreamEvent]:
        with self.bind_execution() as model:
            yield from model.stream(request)
