from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from core.config import settings

Base = declarative_base()

from models.linkedin_account import LinkedInAccount  # noqa: F401
from models.whatsapp import WhatsAppSession, WhatsAppMonitoredGroup, WhatsAppForwardGroup, WhatsAppRawMessage, WhatsAppScanFilter  # noqa: F401

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)

# Create a configured "Session" class.
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Initializes the database and creates tables if they don't exist."""
    async with engine.begin() as conn:
        # This will create all tables defined in models that inherit from Base
        await conn.run_sync(Base.metadata.create_all)
