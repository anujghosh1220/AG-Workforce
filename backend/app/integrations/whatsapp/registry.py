from .base import get_whatsapp_provider
from .meta_cloud import MetaCloudProvider

REGISTRY = {"meta_cloud": MetaCloudProvider}

def available() -> list[dict]:
    p = MetaCloudProvider()
    return [{"name": "meta_cloud", "configured": p.configured(),
             "requires": ["WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]}]
