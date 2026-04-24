from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

    jwt_secret: str
    jwt_exp_days: int = 7
    allowed_emails: str = ""

    google_client_id: str = ""

    cache_ttl_seconds: int = 86400
    internal_secret: str = ""

    frontend_origin: str = "*"

    @property
    def allowed_emails_list(self) -> list[str]:
        return [e.strip() for e in self.allowed_emails.split(",") if e.strip()]


settings = Settings()
