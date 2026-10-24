"""Qdrant collections that know which embedding model filled them.

Vectors from two embedding models do not mix: even at the same dimension the
scores are meaningless, and a query embedded by one model against a collection
embedded by another returns plausible-looking wrong answers with no error. So a
collection must say what filled it, and a model change must be loud.

Qdrant 1.14 (the version this stack pins) has no collection metadata, so the
model is recorded in the **physical name** and the application talks to a
**logical name** that is an alias:

    localai_docs  (alias)  ->  localai_docs__embeddinggemma-2-270m  (collection)

That gives three properties from Qdrant's own primitives: the model is readable
from the name; the vector size is read from the collection's own config, not from
a claim; and a re-embed builds a new physical collection beside the old one and
switches the alias in one atomic call, so rollback is a second switch
(``scripts/reembed.py``).

A *legacy* collection is one named exactly like the logical name, filled before
this existed. Its model was never recorded. An empty one is adopted silently; a
populated one is refused with the command that migrates it, because answering
from it would be a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    Distance,
    VectorParams,
)

SEP = "__"


class CollectionMismatch(RuntimeError):
    """The collection and the configured embedder disagree, or cannot be told apart."""


def model_slug(tag: str) -> str:
    """Ollama tag -> the form used in physical names (``embeddinggemma-2:270m`` -> ``embeddinggemma-2-270m``)."""
    return re.sub(r"[^a-z0-9.]+", "-", tag.lower()).strip("-")


def physical_name(logical: str, model_tag: str) -> str:
    return f"{logical}{SEP}{model_slug(model_tag)}"


def parse_physical(name: str) -> tuple[str, str] | None:
    """``(logical, model_slug)`` for a physical name, else None."""
    logical, sep, slug = name.rpartition(SEP)
    return (logical, slug) if sep and logical and slug else None


@dataclass(frozen=True)
class Resolution:
    logical: str
    kind: str  # "alias" | "legacy" | "absent"
    physical: str | None  # the collection that holds the points
    model_slug: str | None  # None for legacy: never recorded
    dim: int | None
    points: int | None

    @property
    def usable_as_is(self) -> bool:
        return self.kind == "alias"


def _aliases(client: QdrantClient) -> dict[str, str]:
    return {a.alias_name: a.collection_name for a in client.get_aliases().aliases}


def vector_size(client: QdrantClient, collection: str) -> int | None:
    params = client.get_collection(collection).config.params.vectors
    size = getattr(params, "size", None)
    return int(size) if size is not None else None


def resolve(client: QdrantClient, logical: str) -> Resolution:
    aliases = _aliases(client)
    if logical in aliases:
        physical = aliases[logical]
        parsed = parse_physical(physical)
        info = client.get_collection(physical)
        return Resolution(
            logical,
            "alias",
            physical,
            parsed[1] if parsed else None,
            vector_size(client, physical),
            info.points_count,
        )
    if client.collection_exists(logical):
        info = client.get_collection(logical)
        return Resolution(
            logical, "legacy", logical, None, vector_size(client, logical), info.points_count
        )
    return Resolution(logical, "absent", None, None, None, None)


def switch_alias(client: QdrantClient, logical: str, physical: str) -> None:
    """Point ``logical`` at ``physical`` in one atomic call (delete + create)."""
    ops = []
    if logical in _aliases(client):
        ops.append(DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=logical)))
    ops.append(
        CreateAliasOperation(create_alias=CreateAlias(collection_name=physical, alias_name=logical))
    )
    client.update_collection_aliases(change_aliases_operations=ops)


def create_physical(client: QdrantClient, logical: str, model_tag: str, dim: int) -> str:
    name = physical_name(logical, model_tag)
    client.create_collection(name, vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
    return name


def verify(client: QdrantClient, logical: str, model_tag: str, dim: int) -> Resolution:
    """Raise ``CollectionMismatch`` unless ``logical`` is usable with this embedder."""
    res = resolve(client, logical)
    want = model_slug(model_tag)
    if res.kind == "absent":
        return res
    if res.kind == "alias":
        if res.model_slug != want:
            raise CollectionMismatch(
                f"collection '{logical}' holds vectors from '{res.model_slug}' ({res.physical}) but the "
                f"embedder is '{model_tag}'. Their vectors do not mix. Re-embed: "
                f"scripts/reembed.py run {logical}  (or select the old model again)."
            )
        if res.dim != dim:
            raise CollectionMismatch(
                f"collection '{res.physical}' has vector size {res.dim} but the embedder produces {dim}. "
                f"Re-embed: scripts/reembed.py run {logical}."
            )
        return res
    # legacy: the model was never recorded
    if res.points:
        raise CollectionMismatch(
            f"collection '{logical}' holds {res.points} points embedded by an unrecorded model. Answering "
            f"from it would be a guess. Migrate it: scripts/reembed.py run {logical}  "
            "(rebuilds from the source documents behind an alias; a snapshot is kept)."
        )
    return res


def ensure(client: QdrantClient, logical: str, model_tag: str, dim: int) -> Resolution:
    """Return a usable resolution, creating or adopting an empty collection as needed."""
    res = verify(client, logical, model_tag, dim)
    if res.kind == "alias":
        return res
    if res.kind == "legacy":  # empty (verify refused otherwise): replace by the named form
        client.delete_collection(logical)
    physical = create_physical(client, logical, model_tag, dim)
    switch_alias(client, logical, physical)
    return resolve(client, logical)


def inventory(client: QdrantClient, model_tag: str, dim: int, logicals: list[str]) -> list[dict]:
    """One row per logical collection, plus any physical collection the aliases do not point at."""
    rows = []
    aliased = set(_aliases(client).values())
    for logical in logicals:
        res = resolve(client, logical)
        try:
            verify(client, logical, model_tag, dim)
            state, why = ("ok" if res.kind == "alias" else res.kind), None
        except CollectionMismatch as exc:
            state, why = "mismatch", str(exc)
        rows.append(
            {
                "logical": logical,
                "kind": res.kind,
                "physical": res.physical,
                "model": res.model_slug,
                "dim": res.dim,
                "points": res.points,
                "state": state,
                "why": why,
            }
        )
    for c in client.get_collections().collections:
        parsed = parse_physical(c.name)
        if parsed and c.name not in aliased and parsed[0] in logicals:
            info = client.get_collection(c.name)
            rows.append(
                {
                    "logical": parsed[0],
                    "kind": "inactive",
                    "physical": c.name,
                    "model": parsed[1],
                    "dim": vector_size(client, c.name),
                    "points": info.points_count,
                    "state": "inactive",
                    "why": None,
                }
            )
    return rows
