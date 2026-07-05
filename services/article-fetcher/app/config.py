from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    redis_url: str = "redis://redis:6379/0"
    minio_endpoint: str = "articles-minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "articles"
    minio_region: str = "ru-msk"
    minio_public_endpoint: str = "http://localhost:9092"
    article_processor_webhook_url: str = ""
    # Timeout for the pdf-parser webhook POST. pdf-parser's POST /jobs only
    # enqueues and returns 202, so 30s connect+read is ample headroom while
    # tolerating transient load. A previous 5s default caused ReadTimeouts that
    # silently dropped parse jobs (paper never parsed -> grounding failed).
    webhook_timeout: float = 30.0

    # OpenAlex PDF downloader (preferred fetch path, ahead of Sci-Hub).
    # Empty defaults keep the feature inert until env injects credentials.
    openalex_api_key: str = ""
    openalex_api_key_2: str = ""
    openalex_api_base: str = "https://api.openalex.org"
    openalex_mailto: str = ""
    # Daily cap on paid "managed content" PDF downloads (free tier = 100/day).
    openalex_content_daily_cap: int = 100

    # Cyberleninka RU-language search (returns full text inline via `ocr` — no
    # separate PDF fetch needed). Only reachable from this box via the socks5
    # proxy below; empty string disables the proxy (client falls back to a
    # direct request either way on proxy failure).
    cyberleninka_api_base: str = "https://cyberleninka.ru/api"
    cyberleninka_proxy_url: str = "socks5h://37.16.81.138:1080"

    # Sci-Hub mirror fallback (tried in order, after the OpenAlex OA path).
    # Comma-separated + env-overridable (SCIHUB_MIRRORS). Default expanded from the
    # old 2-mirror list — all verified reachable (sci-hub.se is dead/NXDOMAIN and
    # omitted). More mirrors = more retry surface when a given mirror flaps/times out.
    scihub_mirrors: str = (
        "https://sci-hub.ru,https://sci-hub.ee,https://sci-hub.st,"
        "https://sci-hub.wf,https://sci-hub.ren"
    )

    # How long completed download jobs (and their presign-able object keys) stay
    # in the Redis registry. Must match the backend DISCOVERY_PAPER_RETENTION_DAYS
    # so discovery PDF links survive long after the run. MinIO objects have no TTL.
    job_retention_days: int = 365

    # --- STC / Nexus (libstc.cc) IPFS source --------------------------------
    # Opt-in last-resort PDF source backed by the Nexus/STC IPFS corpus, served
    # through a local Kubo gateway. Default OFF: when False, download_pdf_via_stc
    # returns None immediately and the fetch chain is byte-for-byte unchanged.
    # libstc-geck is an OPTIONAL dependency imported lazily; a missing dep also
    # degrades to None. Standing up the Kubo gateway is a separate ops step.
    stc_enabled: bool = False
    ipfs_gateway_url: str = "http://ipfs:8080"
    stc_index_alias: str = "nexus_science"
    stc_timeout: int = 60

    # --- Anna's Archive SciDB source ----------------------------------------
    # Keyless DOI -> PDF source backed by Anna's Archive SciDB, which aggregates
    # Sci-Hub + LibGen + Z-Library + Nexus — the practical superset of "available
    # on Sci-Hub". Tried AFTER OpenAlex-OA and BEFORE the Sci-Hub mirror loop in
    # ``fetcher.fetch_article``. Default ON (strong, no-key source); the flag lets
    # us disable it. The ``.gl`` mirror is reachable from this VM (the .org/.se
    # domains are IPv6-only / geo-DNS here). When False, download_pdf_via_scidb
    # returns None immediately and the fetch chain is byte-for-byte unchanged.
    scidb_enabled: bool = True
    scidb_mirror: str = "https://annas-archive.gl"
    scidb_timeout: int = 45

    # --- Headless stealth-browser PDF fetch tier ----------------------------
    # Last-resort direct-URL tier that launches a patched stealth Firefox
    # (invisible_playwright) to execute the Akamai/Cloudflare JS interstitial
    # that gold-OA publishers (MDPI, Hindawi) wall their OA PDFs behind — a
    # challenge curl_cffi cannot solve. Default OFF: when False the headless
    # module is never imported and the fetch chain is byte-for-byte unchanged.
    # The patched Firefox binary is heavy (~hundreds of MB baked into the image)
    # which is why this tier is flag-gated. Concurrency is capped so we never run
    # more than a couple of Firefox instances at once (memory pressure).
    # ``headless_fetch_timeout`` is now a TOTAL wall-clock budget for one solve
    # (goto + clearance-wait + PDF fetch share it via a single deadline), NOT a
    # per-Playwright-step timeout. Bumped 45 -> 60 so a single slow paper is still
    # bounded to <=60s while leaving headroom for a contended cold-start solve.
    headless_fetch_enabled: bool = False
    headless_fetch_max_concurrency: int = 1
    headless_fetch_timeout: int = 60

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def scihub_mirror_list(self) -> list[str]:
        """Ordered, deduped, non-empty Sci-Hub mirror base URLs (trailing slash stripped)."""
        seen: set[str] = set()
        out: list[str] = []
        for m in self.scihub_mirrors.split(","):
            m = m.strip().rstrip("/")
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    @property
    def openalex_api_keys(self) -> list[str]:
        """Ordered, deduped, non-empty OpenAlex keys for failover.

        The article-fetcher PREFERS the second key (key2 first) to keep a
        load-split preference away from the backend search path, which prefers
        the primary key. A single configured key behaves exactly as before.
        """
        ordered = [self.openalex_api_key_2, self.openalex_api_key]
        seen: set[str] = set()
        out: list[str] = []
        for key in ordered:
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out


settings = Settings()
