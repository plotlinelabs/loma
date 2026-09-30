"""Seed skills: all on an empty DB; on existing deployments only AUTO_ADD_SLUGS, never overwriting."""
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
async def test_existing_deployments_only_get_auto_add_skills(seed_dir):
    db = AsyncMongoMockClient()['seed_test_existing']
    await skill_seed.seed_default_skills(db)
    # A user edited alpha and deleted (disabled) mobile-e2e; later a new bundled skill ships.
    await db.skill_files.update_one({'skill_slug': 'alpha', 'path': 'SKILL.md'}, {'$set': {'content': 'edited'}})
    await db.skills.update_one({'slug': 'mobile-e2e'}, {'$set': {'enabled': False}})
    _skill(seed_dir, 'beta', 'bundled beta')
    (seed_dir / 'alpha' / 'SKILL.md').write_text('---\nname: alpha\ndescription: v2\n---\n\nnew bundled text\n')

    await skill_seed.seed_default_skills(db)

    # beta is not opted in to auto-add: a populated deployment does not get it.
    assert {d['slug'] async for d in db.skills.find({})} == {'alpha', 'mobile-e2e'}
    assert await _skill_md(db, 'alpha') == 'edited'
    assert (await db.skills.find_one({'slug': 'mobile-e2e'}))['enabled'] is False


@pytest.mark.asyncio
async def test_auto_add_skill_reaches_a_deployment_seeded_before_it(seed_dir):
    db = AsyncMongoMockClient()['seed_test_auto_add']
    await skill_seed.seed_default_skills(db)
    await db.skills.delete_one({'slug': 'mobile-e2e'})  # as if seeded before mobile-e2e was bundled
    await db.skill_files.delete_many({'skill_slug': 'mobile-e2e'})
    _skill(seed_dir, 'beta', 'bundled beta')

    await skill_seed.seed_default_skills(db)

    assert {d['slug'] async for d in db.skills.find({})} == {'alpha', 'mobile-e2e'}
    assert 'bundled mobile' in await _skill_md(db, 'mobile-e2e')


@pytest.mark.asyncio
async def test_off_switch(seed_dir, monkeypatch):
    monkeypatch.setattr(skill_seed, 'LOMA_SEED_SKILLS', False)
    db = AsyncMongoMockClient()['seed_test_off']
    await skill_seed.seed_default_skills(db)
    assert await db.skills.count_documents({}) == 0
