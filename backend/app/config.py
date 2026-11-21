from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """集中配置：全部从环境变量/.env 读取，12-Factor 风格。"""

    mysql_host: str = "mysql"
    mysql_port: int = 3306
    mysql_user: str = "llmhub"
    mysql_password: str = "llmhub_2026"
    mysql_db: str = "llmhub"

    jwt_secret: str = "dev-secret-change-me-in-prod"
    jwt_expire_minutes: int = 120

    deepseek_key: str = ""
    glm_key: str = ""
    qwen_key: str = ""
    siliflow_key: str = ""

    redis_url: str = "redis://redis:6379/0"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )


settings = Settings()
