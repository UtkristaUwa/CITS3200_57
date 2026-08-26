from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_cloud_project: str = "tenderai-dev"
    bigquery_dataset: str = "TenderAI"
    allowed_origins: str
    use_mock_data: bool = False

    @property
    def tenders_table(self) -> str:
        return f"{self.google_cloud_project}.{self.bigquery_dataset}.tenders"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()
