"""Authoritative household profiles stored atomically beside private face templates."""
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import AppError

MAX_MEMBERS = 32
MAX_BINDINGS = 32


def validate_member_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value):
        raise AppError('HOUSEHOLD_MEMBER_INVALID', '成员 ID 须为 1–80 位字母、数字、下划线或连字符。', 422)
    return value


class MemberBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    device_id: str = Field(min_length=1, max_length=128)
    scope: str = Field(min_length=1, max_length=512)
    track_id: str = Field(default='', max_length=128)

    @field_validator('device_id', 'scope', 'track_id')
    @classmethod
    def safe_text(cls, value, info):
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or (info.field_name != 'track_id' and not value.strip()):
            raise ValueError('Binding contains an empty value or control character')
        return value


class MemberProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    number: int = Field(ge=1, le=2147483647)
    name: str = Field(max_length=80)
    relationship: str = Field(max_length=80)
    notes: str = Field(max_length=2000)
    bindings: list[MemberBinding] = Field(max_length=MAX_BINDINGS)
    # Accept the complete Flutter toJson() shape, but never trust the client count.
    face_sample_count: int | None = Field(default=None, ge=0, le=2147483647)
    revision: int | None = Field(default=None, ge=0, le=9223372036854775806)

    @field_validator('name', 'relationship', 'notes')
    @classmethod
    def safe_text(cls, value, info):
        permitted = '\n\r\t' if info.field_name == 'notes' else ''
        if any((ord(c) < 32 and c not in permitted) or ord(c) == 127 for c in value):
            raise ValueError('Text contains a control character')
        return value

    @field_validator('bindings')
    @classmethod
    def unique_bindings(cls, values):
        keys = [(b.device_id, b.scope, b.track_id) for b in values]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate bindings')
        return values


def display_name(profile):
    return profile['name'].strip() or f"成员{profile['number']}"


def initialize_profiles(c):
    c.execute('''CREATE TABLE IF NOT EXISTS household_members (
        id TEXT PRIMARY KEY, number INTEGER NOT NULL, name TEXT NOT NULL,
        relationship TEXT NOT NULL, notes TEXT NOT NULL, bindings TEXT NOT NULL,
        revision INTEGER NOT NULL)''')
    # Retain only ID/version tombstones, so delete/recreate cannot accept an old revision.
    c.execute('CREATE TABLE IF NOT EXISTS household_versions (id TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
    legacy = c.execute('''SELECT id,name FROM members WHERE id NOT IN
        (SELECT id FROM household_members) AND id NOT IN (SELECT id FROM household_versions) ORDER BY id''').fetchall()
    number = c.execute('SELECT COALESCE(MAX(number),0) FROM household_members').fetchone()[0]
    for row in legacy:
        number += 1
        c.execute("INSERT INTO household_members VALUES(?,?,?,'','','[]',1)", (row['id'], number, row['name']))
        c.execute('INSERT INTO household_versions VALUES(?,1)', (row['id'],))


def bump_revision(c, member_id):
    c.execute('''INSERT INTO household_versions VALUES(?,1)
        ON CONFLICT(id) DO UPDATE SET revision=revision+1''', (member_id,))
    revision = c.execute('SELECT revision FROM household_versions WHERE id=?', (member_id,)).fetchone()[0]
    c.execute('UPDATE household_members SET revision=? WHERE id=?', (revision, member_id))
    return revision


class MemberProfiles:
    def __init__(self, faces):
        self.faces = faces

    def _read(self, c, member_id=None):
        rows = c.execute('''SELECT p.*, (SELECT COUNT(*) FROM samples s WHERE s.member_id=p.id AND s.model=?)
            AS face_sample_count FROM household_members p''' + (' WHERE p.id=?' if member_id is not None else '')
            + ' ORDER BY p.number,p.id', (self.faces.model_version, member_id) if member_id is not None
            else (self.faces.model_version,)).fetchall()
        return [{**dict(row), 'bindings': json.loads(row['bindings'])} for row in rows]

    def members(self):
        with self.faces.connect() as c:
            return self._read(c)

    def upsert(self, member_id, profile):
        validate_member_id(member_id)
        if not isinstance(profile, MemberProfileInput):
            profile = MemberProfileInput.model_validate(profile)
        if member_id != profile.id:
            raise AppError('HOUSEHOLD_ID_MISMATCH', '路径与档案的成员 ID 必须一致。', 422)
        with self.faces.connect() as c:
            existing = c.execute('SELECT revision FROM household_members WHERE id=?', (member_id,)).fetchone()
            if profile.revision is not None and profile.revision != (existing['revision'] if existing else 0):
                raise AppError('HOUSEHOLD_REVISION_CONFLICT', '档案已更新或删除，请刷新后重试。', 409)
            if not existing and c.execute('SELECT COUNT(*) FROM household_members').fetchone()[0] >= MAX_MEMBERS:
                raise AppError('HOUSEHOLD_MEMBER_LIMIT', '家庭成员数量已达 32 人上限。', 409)
            revision = bump_revision(c, member_id)
            c.execute('''INSERT INTO household_members VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                number=excluded.number,name=excluded.name,relationship=excluded.relationship,
                notes=excluded.notes,bindings=excluded.bindings,revision=excluded.revision''',
                (member_id, profile.number, profile.name, profile.relationship, profile.notes,
                 json.dumps([b.model_dump() for b in profile.bindings], ensure_ascii=False), revision))
            c.execute('UPDATE members SET name=? WHERE id=?',
                      (display_name({'name': profile.name, 'number': profile.number}), member_id))
            return self._read(c, member_id)[0]

    def delete(self, member_id, revision=None):
        validate_member_id(member_id)
        with self.faces.connect() as c:
            exists = c.execute('SELECT revision FROM household_members WHERE id=?', (member_id,)).fetchone()
            if revision is not None and revision != (exists['revision'] if exists else 0):
                raise AppError('HOUSEHOLD_REVISION_CONFLICT', '档案已更新或删除，请刷新后重试。', 409)
            if exists:
                bump_revision(c, member_id)
                c.execute('DELETE FROM members WHERE id=?', (member_id,))
                c.execute('DELETE FROM household_members WHERE id=?', (member_id,))
            return {'member_id': member_id, 'deleted': bool(exists)}
