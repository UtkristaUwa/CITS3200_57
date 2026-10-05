from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_cloud_project: str = "tenderai-dev"
    bigquery_dataset: str = "TenderAI"
    allowed_origins: str
    use_mock_data: bool = False

    # Shared runtime tender processor configuration. These are intentionally
    # empty by default: deployments must opt in explicitly and no production
    # bucket or object name belongs in application code.
    runtime_config_bucket: str = ""
    runtime_config_object: str = ""

    # Entra ID SSO. Auto-provisioning trusts the tenant boundary enforced by
    # the single-tenant Entra app registration; the domain list is optional
    # defence in depth (comma-separated, e.g. "sva.com.au,uwa.edu.au").
    auto_provision_sso: bool = True
    allowed_email_domains: str = ""

    @property
    def tenders_table(self) -> str:
        return f"{self.google_cloud_project}.{self.bigquery_dataset}.tenders"

    @property
    def allowed_email_domains_list(self) -> list[str]:
        return [
            domain.strip().lower().lstrip("@")
            for domain in self.allowed_email_domains.split(",")
            if domain.strip()
        ]

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()
