import json

from app.ai.base import LLMUnavailable
from app.database.database import session_scope
from app.database.models import Memory, User
from app.database.repositories import memories as repo
from app.memory.extractor import MemoryExtractor, parse_extraction, worth_extracting
from app.memory.manager import MemoryManager
from tests.conftest import FakeProvider


def test_create_dedupe_and_retrieve(uid):
    with session_scope() as s:
        u = s.get(User, uid)
        _, c1 = repo.add_memory(s, u, "Exam is on November 12", "important_date", 0.9, 0.9)
        _, c2 = repo.add_memory(s, u, "My exam is on November 12th", "important_date")
        _, c3 = repo.add_memory(s, u, "Likes jazz music", "preference")
        assert (c1, c2, c3) == (True, False, True)
        hits = repo.search_memories(s, u, "when is my exam")
        assert hits and "exam" in hits[0].content
        assert repo.search_memories(s, u, "quantum chromodynamics") == []


def test_context_memories_include_important_even_without_match(uid):
    with session_scope() as s:
        u = s.get(User, uid)
        repo.add_memory(s, u, "Hates being called by full name", "communication", 0.9, 0.9)
        assert repo.context_memories(s, u, "hello there", 5)


def test_forget_and_list(uid):
    mgr = MemoryManager()
    mgr.remember(uid, "Lives in Karachi", "personal_fact")
    assert "Karachi" in mgr.list_text(uid)
    assert mgr.forget_last(uid) == "Lives in Karachi"
    assert "don't have anything" in mgr.list_text(uid)


def test_prefilter_skips_chitchat():
    assert not worth_extracting("lol ok")
    assert not worth_extracting("what's the weather?")
    assert worth_extracting("I hate being called by my full name.")


def test_parse_extraction_tolerates_garbage():
    assert parse_extraction("not json") == []
    assert parse_extraction("") == []
    assert parse_extraction('{"memories": "oops"}') == []
    good = '```json\n{"memories":[{"content":"Likes tea","category":"preference"},{"nope":1}]}\n```'
    assert len(parse_extraction(good)) == 1


async def test_extractor_saves_only_confident_important(uid):
    payload = json.dumps({"memories": [
        {"content": "Dislikes being called by full name", "category": "communication", "importance": 0.8, "confidence": 0.95},
        {"content": "Maybe likes toast", "category": "preference", "importance": 0.2, "confidence": 0.9},
        {"content": "Unsure thing", "category": "fact", "importance": 0.9, "confidence": 0.3},
    ]})
    ex = MemoryExtractor(FakeProvider([payload]), MemoryManager())
    saved = await ex.extract_and_save(uid, "I hate being called by my full name.")
    assert saved == ["Dislikes being called by full name"]
    with session_scope() as s:
        assert s.query(Memory).count() == 1


async def test_extractor_survives_model_outage_and_bad_json(uid):
    ex = MemoryExtractor(FakeProvider([LLMUnavailable("down")]), MemoryManager())
    assert await ex.extract_and_save(uid, "My exam is on November 12") == []
    ex = MemoryExtractor(FakeProvider(["{{{ broken"]), MemoryManager())
    assert await ex.extract_and_save(uid, "My exam is on November 12") == []
