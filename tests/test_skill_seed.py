"""Bundled seed skills reach existing deployments without touching existing skills."""
import pytest
from mongomock_motor import AsyncMongoMockClient

from api import skill_seed


def _skill(root, name, body):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: {body}\n---\n\n# {name}\n\n{body}\n')


@pytest.fixture
def seed_dir(tmp_path, monkeypatch):
    root = tmp_path / 'seed'
    _skill(root, 'alpha', 'bundled alpha')
    _skill(root, 'mobile-e2e', 'bundled mobile')
    monkeypatch.setattr(skill_seed, 'SEED_DIR', root)
    monkeypatch.setattr(skill_seed, 'LOMA_SEED_SKILLS', True)
    return root


async def _skill_md(db, slug):
    doc = await db.skill_files.find_one({'skill_slug': slug, 'path': 'SKILL.md', 'deleted': {'$ne': True}})
    return doc and doc.get('content')


@pytest.mark.asyncio
async def test_seeds_empty_collection(seed_dir):
    db = AsyncMongoMockClient()['seed_test_empty']
    await skill_seed.seed_default_skills(db)
    assert {d['slug'] async for d in db.skills.find({})} == {'alpha', 'mobile-e2e'}


@pytest.mark.asyncio
async def test_adds_only_missing_skills_and_never_overwrites(seed_dir):
    db = AsyncMongoMockClient()['seed_test_existing']
    await skill_seed.seed_default_skills(db)
    # A user edited alpha and deleted (disabled) mobile-e2e; later a new bundled skill ships.
    await db.skill_files.update_one({'skill_slug': 'alpha', 'path': 'SKILL.md'}, {'$set': {'content': 'edited'}})
    await db.skills.update_one({'slug': 'mobile-e2e'}, {'$set': {'enabled': False}})
    _skill(seed_dir, 'beta', 'bundled beta')
    (seed_dir / 'alpha' / 'SKILL.md').write_text('---\nname: alpha\ndescription: v2\n---\n\nnew bundled text\n')

    await skill_seed.seed_default_skills(db)

    assert {d['slug'] async for d in db.skills.find({})} == {'alpha', 'mobile-e2e', 'beta'}
    assert await _skill_md(db, 'alpha') == 'edited'
    assert (await db.skills.find_one({'slug': 'mobile-e2e'}))['enabled'] is False
    assert 'bundled beta' in await _skill_md(db, 'beta')


@pytest.mark.asyncio
async def test_off_switch(seed_dir, monkeypatch):
    monkeypatch.setattr(skill_seed, 'LOMA_SEED_SKILLS', False)
    db = AsyncMongoMockClient()['seed_test_off']
    await skill_seed.seed_default_skills(db)
    assert await db.skills.count_documents({}) == 0
