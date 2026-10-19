from contextlib import contextmanager
from .agent import ChatLLM, KeywordPlanner, QualificationAgent
from .clients import JsonHTTP, JustOneAPI
from .collectors import Collector
from .config import settings
from .workflow import DiscoveryService
from .storage import Store


@contextmanager
def runtime(db=None, analyze=True, trace=None):
    config = settings()
    store = Store(db or config['DISCOVERY_DB'] or 'data/discovery.sqlite3')
    http = JsonHTTP()
    try:
        api = JustOneAPI(config['JUSTONEAPI_TOKEN'], http)
        llm = ChatLLM(http,
            config['LLM_BASE_URL'], config['LLM_API_KEY'], config['LLM_MODEL'], trace)
        agent = None if not analyze else QualificationAgent(http,
            config['LLM_BASE_URL'], config['LLM_API_KEY'], config['LLM_MODEL'], trace)
        yield DiscoveryService(Collector(api), KeywordPlanner(llm), llm, agent,
                               store, 'live')
    finally:
        http.close()
        store.close()
