"""JobControlMixin — opt-in async-job control for OOP gateway servers.

Compose this onto a :class:`BaseGatewayServer` subclass to turn it into a
"polling" server: it auto-registers ``<prefix>job_status`` and
``<prefix>job_cancel`` tools and provides :meth:`start_job` /
:meth:`started_message` helpers that apply the server's ETA defaults. Servers
that don't run long tasks simply omit the mixin and carry no job machinery.

Place the mixin BEFORE the base in the MRO so its ``_register_tools`` runs::

    class GradleServer(JobControlMixin, BaseGatewayServer):
        name = "gradle"
        job_tool_prefix = "gradle_"
        eta_defaults = {"build": 240, "test": 180}

        def _register_tools(self):
            super()._register_tools()          # registers job_status + job_cancel
            @self.tool()
            def gradle_build(project_root: str) -> str:
                jid = self.start_job("gradle:build", _fn, project_root, eta_bucket="build")
                return self.started_message(jid)

Registry scoping: this mixin delegates to the module-level
:mod:`archolith_mcp_framework.jobs` registry. Each MCP server runs as its own stdio
process, so that process-global registry is effectively per-server; an
instance-scoped registry would buy isolation only inside a hypothetical
multi-server process and is intentionally not used here.
"""

from __future__ import annotations

from archolith_mcp_framework import jobs


class JobControlMixin:
    """Adds background-job control + ETA to a BaseGatewayServer subclass."""

    #: Cold-start ETA defaults in seconds, keyed by eta_bucket.
    eta_defaults: dict[str, float] = {}

    #: Prefix for the registered job tools, e.g. "gradle_" -> "gradle_job_status".
    job_tool_prefix: str = ""

    #: ETA used when an eta_bucket is not present in eta_defaults.
    eta_default_fallback: float = 120.0

    # ------------------------------------------------------------------
    # Job helpers
    # ------------------------------------------------------------------

    def start_job(
        self,
        label: str,
        fn,
        *args,
        eta_bucket: str | None = None,
        eta_tool: str | None = None,
        eta_default: float | None = None,
        **kwargs,
    ) -> str:
        """Start a background job, applying this server's ETA defaults.

        When ``eta_bucket`` is given, ``eta_default`` falls back to
        ``eta_defaults[bucket]`` (then :attr:`eta_default_fallback`) and
        ``eta_tool`` defaults to ``f"{job_tool_prefix}{bucket}"``. All other
        keyword args (``streaming``, ``timeout_s``, ...) pass straight through to
        :func:`archolith_mcp_framework.jobs.start_job`.
        """
        if eta_bucket is not None:
            if eta_default is None:
                eta_default = self.eta_defaults.get(eta_bucket, self.eta_default_fallback)
            if eta_tool is None:
                eta_tool = f"{self.job_tool_prefix}{eta_bucket}"
            kwargs["eta_bucket"] = eta_bucket
            kwargs["eta_tool"] = eta_tool
            kwargs["eta_default"] = eta_default
        return jobs.start_job(label, fn, *args, **kwargs)

    def job_eta(self, job_id: str):
        """Return the current DurationEstimate for an ETA-tracked job, or None."""
        return jobs.job_eta(job_id)

    def started_message(self, job_id: str, *, poll_tool: str | None = None) -> str:
        """Standard 'job started' message with the wait-once-then-check ETA line."""
        poll = poll_tool or f"{self.job_tool_prefix}job_status"
        est = jobs.job_eta(job_id)
        suffix = f"\n{est.guidance()}" if est is not None else ""
        return (
            f"Job started: {job_id}\n"
            f"Poll with {poll}(job_id='{job_id}', status_only=true); pass "
            f"status_only=false with since_line=<next_line> for new output.{suffix}"
        )

    # ------------------------------------------------------------------
    # Tool registration (cooperative)
    # ------------------------------------------------------------------

    def _register_tools(self) -> None:
        super()._register_tools()  # type: ignore[misc]
        self._register_job_tools()

    def _register_job_tools(self) -> None:
        prefix = self.job_tool_prefix

        def job_status(
            job_id: str | None = None,
            since_line: int = 0,
            status_only: bool = True,
        ) -> str:
            """Poll a background job (or list jobs when job_id is omitted).

            Token-efficient: after starting a job, wait the ETA from the start
            message, then call this ONCE. Pass status_only=false with
            since_line=<next_line from the previous poll> to fetch only new
            output. Investigate early only if the status shows STUCK.
            """
            return jobs.job_status(job_id, since_line=since_line, status_only=status_only)

        def job_cancel(job_id: str) -> str:
            """Kill a running background job by its job_id."""
            return jobs.cancel_job(job_id)

        self.mcp.tool(name=f"{prefix}job_status")(job_status)  # type: ignore[attr-defined]
        self.mcp.tool(name=f"{prefix}job_cancel")(job_cancel)  # type: ignore[attr-defined]
