"""Harbor Codex adapter that preserves the full OpenRouter model ID."""

import shlex
from typing import override

from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths


class OpenRouterCodex(Codex):
    """Run Codex against OpenRouter without truncating ``provider/model``."""

    @staticmethod
    @override
    def name() -> str:
        return "codex-openrouter"

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        escaped_instruction = shlex.quote(instruction)

        if not self.model_name:
            raise ValueError("Model name is required")

        # Harbor's built-in adapter strips provider prefixes for first-party
        # Codex models. OpenRouter requires the complete model slug.
        model = self.model_name

        cli_flags = self.build_cli_flags()
        cli_flags_arg = (cli_flags + " ") if cli_flags else ""

        auth_json_path = self._resolve_auth_json_path()
        access = self.model_connection

        remote_codex_home = self._REMOTE_CODEX_HOME.as_posix()
        remote_secrets_dir = self._REMOTE_CODEX_SECRETS_DIR.as_posix()
        remote_auth_path = (self._REMOTE_CODEX_SECRETS_DIR / "auth.json").as_posix()
        remote_config_path = (self._REMOTE_CODEX_HOME / "config.toml").as_posix()
        agent_sessions_dir = (EnvironmentPaths.agent_dir / "sessions").as_posix()

        env: dict[str, str] = {"CODEX_HOME": remote_codex_home}

        await self.exec_as_agent(
            environment,
            command=(
                f'mkdir -p "$CODEX_HOME" {shlex.quote(remote_secrets_dir)} '
                f"{shlex.quote(EnvironmentPaths.agent_dir.as_posix())}"
            ),
            env=env,
        )

        if self._load:
            await self._seed_load_trajectory(environment)
        resume_run = self._resume or self._load

        if auth_json_path:
            await environment.upload_file(auth_json_path, remote_auth_path)
            if environment.default_user is not None:
                await self.exec_as_root(
                    environment,
                    command=f"chown {environment.default_user} {remote_auth_path}",
                )
            setup_command = (
                f"ln -sf {shlex.quote(remote_auth_path)} \"$CODEX_HOME/auth.json\"\n"
            )
        else:
            env["OPENAI_API_KEY"] = access.api_key or ""
            setup_command = (
                f"cat >{shlex.quote(remote_auth_path)} <<EOF\n"
                '{\n  "OPENAI_API_KEY": "${OPENAI_API_KEY}"\n}\nEOF\n'
                f"ln -sf {shlex.quote(remote_auth_path)} "
                '"$CODEX_HOME/auth.json"\n'
            )

        openai_base_url = access.configured_base_url
        if openai_base_url:
            env["OPENAI_BASE_URL"] = openai_base_url

        effective_config = self._build_effective_config(openai_base_url)
        await self._upload_effective_config(
            environment,
            effective_config,
            remote_config_path,
        )

        skills_command = self._build_register_skills_command()
        if skills_command:
            setup_command += f"\n{skills_command}"

        if resume_run:
            setup_command += (
                f"\nif [ ! -d {shlex.quote(agent_sessions_dir)} ]; then\n"
                '  echo "Cannot resume Codex: no previous session logs found" >&2\n'
                "  exit 1\n"
                "fi\n"
                'rm -rf "$CODEX_HOME/sessions"\n'
                f"cp -R {shlex.quote(agent_sessions_dir)} "
                '"$CODEX_HOME/sessions"'
            )

        if setup_command.strip():
            await self.exec_as_agent(environment, command=setup_command, env=env)

        try:
            await self.exec_as_agent(
                environment,
                command=(
                    "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
                    f"codex exec {'resume --last ' if resume_run else ''}"
                    "--dangerously-bypass-approvals-and-sandbox "
                    "--skip-git-repo-check "
                    f"--model {shlex.quote(model)} "
                    "--json "
                    "--enable unified_exec "
                    f"{cli_flags_arg}"
                    "-- "
                    f"{escaped_instruction} "
                    f"2>&1 | tee {EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME}"
                ),
                env=env,
            )
        finally:
            try:
                await self.exec_as_agent(
                    environment,
                    command=(
                        f"mkdir -p {EnvironmentPaths.agent_dir.as_posix()}\n"
                        'if [ -d "$CODEX_HOME/sessions" ]; then\n'
                        f"  rm -rf {(EnvironmentPaths.agent_dir / 'sessions').as_posix()}\n"
                        f"  cp -R \"$CODEX_HOME/sessions\" "
                        f"{(EnvironmentPaths.agent_dir / 'sessions').as_posix()}\n"
                        "fi"
                    ),
                    env=env,
                )
            except Exception:
                pass
            try:
                await self.exec_as_agent(
                    environment,
                    command=f'rm -rf {shlex.quote(remote_secrets_dir)} "$CODEX_HOME"',
                    env=env,
                )
            except Exception:
                pass
