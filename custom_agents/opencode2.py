"""Harbor adapter for the pinned OpenCode 2 beta CLI."""

from pathlib import Path
import shlex
from typing import override

from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.opencode import OpenCode
from harbor.agents.installed.opencode import nvm_node_install_snippet
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class OpenCode2(OpenCode):
    """Run OpenCode 2 headlessly while retaining Harbor's ATIF conversion."""

    DEFAULT_VERSION = "0.0.0-beta-17927"
    _BINARY = "opencode2"
    _OUTPUT_FILENAME = "opencode2.txt"

    @staticmethod
    @override
    def name() -> str:
        return "opencode2"

    def __init__(self, *args, version: str | None = None, **kwargs):
        super().__init__(*args, version=version or self.DEFAULT_VERSION, **kwargs)

    @override
    def get_version_command(self) -> str:
        return f"{self._BINARY} --version"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(
            environment, ("curl", "bash", "coreutils", "nodejs", "npm")
        )
        version_spec = f"@{self._version or self.DEFAULT_VERSION}"
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "if ldd --version 2>&1 | grep -qi musl || "
                "[ -f /etc/alpine-release ]; then "
                "node --version && npm --version; "
                f"else {nvm_node_install_snippet()}; fi && "
                f"npm install -g @opencode-ai/cli{version_spec} && "
                f"{self._BINARY} --version"
            ),
        )

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        self._instruction = instruction
        escaped_instruction = shlex.quote(instruction)

        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must be in the format provider/model_name")

        env = dict(self.model_connection.env)
        env["OPENCODE_FAKE_VCS"] = "git"
        env["XDG_DATA_HOME"] = "/logs/agent/opencode2/xdg-data"
        env["XDG_STATE_HOME"] = "/logs/agent/opencode2/xdg-state"

        skills_command = self._build_register_skills_command()
        if skills_command:
            await self.exec_as_agent(environment, command=skills_command, env=env)

        config_command = self._build_register_config_command()
        if config_command:
            await self.exec_as_agent(environment, command=config_command, env=env)

        cli_flags = self.build_cli_flags()
        cli_flags_arg = f"{cli_flags} " if cli_flags else ""
        resume_flag = "--continue " if self._resume else ""
        output_path = "/logs/agent/opencode2.txt"

        await self.exec_as_agent(
            environment,
            command=(
                "[ -f ~/.nvm/nvm.sh ] && . ~/.nvm/nvm.sh; "
                f"{self._BINARY} --model={shlex.quote(self.model_name)} run "
                f"--format=json {resume_flag}{cli_flags_arg}"
                f"--thinking --dangerously-skip-permissions -- {escaped_instruction} "
                f"2>&1 | stdbuf -oL tee {output_path}"
            ),
            env=env,
        )

        if messages := self._error_messages():
            from harbor.agents.installed.base import NonZeroAgentExitCodeError

            raise NonZeroAgentExitCodeError(
                "OpenCode 2 emitted error event(s): " + "; ".join(messages[:3])
            )
