"""This module contains functions for patching session data in the 'cozodb' database using datalog queries."""

from typing import Any, List, TypeVar
from uuid import UUID

from beartype import beartype
from fastapi import HTTPException
from pycozo.client import QueryException
from pydantic import ValidationError

from ...autogen.openapi_model import PatchSessionRequest, ResourceUpdatedResponse
from ...common.utils.cozo import cozo_process_mutate_data
from ..utils import (
    cozo_query,
    partialclass,
    rewrap_exceptions,
    verify_developer_id_query,
    verify_developer_owns_resource_query,
    wrap_in_class,
)

ModelT = TypeVar("ModelT", bound=Any)
T = TypeVar("T")

_fields: List[str] = [
    "situation",
    "summary",
    "created_at",
    "session_id",
    "developer_id",
]


# TODO: Add support for updating `render_templates` field


@rewrap_exceptions(
    {
        QueryException: partialclass(HTTPException, status_code=400),
        ValidationError: partialclass(HTTPException, status_code=400),
        TypeError: partialclass(HTTPException, status_code=400),
    }
)
@wrap_in_class(
    ResourceUpdatedResponse,
    one=True,
    transform=lambda d: {
        "id": d["session_id"],
        "updated_at": d.pop("updated_at")[0],
        "jobs": [],
        **d,
    },
    _kind="inserted",
)
@cozo_query
@beartype
def patch_session(
    *,
    session_id: UUID,
    developer_id: UUID,
    data: PatchSessionRequest,
) -> tuple[list[str], dict]:
    """Patch session data in the 'cozodb' database.

    Parameters:
    - session_id (UUID): The unique identifier for the session to be updated.
    - developer_id (UUID): The unique identifier for the developer making the update.
    - data (PatchSessionRequest): The request payload containing the updates to apply.
    """

    update_data = data.model_dump(exclude_unset=True)
    metadata = update_data.pop("metadata", {}) or {}

    session_update_cols, session_update_vals = cozo_process_mutate_data(
        {k: v for k, v in update_data.items() if v is not None}
    )

    # Prepare lists of columns for the query.
    session_update_cols_lst = session_update_cols.split(",")
    all_fields_lst = list(set(session_update_cols_lst).union(set(_fields)))
    all_fields = ", ".join(all_fields_lst)
    rest_fields = ", ".join(
        list(
            set(all_fields_lst)
            - set([k for k, v in update_data.items() if v is not None])
        )
    )

    # Construct the datalog query for updating session information.
    update_query = f"""
        input[{session_update_cols}] <- $session_update_vals
        ids[session_id, developer_id] <- [[to_uuid($session_id), to_uuid($developer_id)]]
        
        ?[{all_fields}, metadata, updated_at] :=
            input[{session_update_cols}],
            ids[session_id, developer_id],
            *sessions{{
                {rest_fields}, metadata: md, @ "END"
            }},
            updated_at = 'ASSERT',
            metadata = concat(md, $metadata),

        :put sessions {{
            {all_fields}, metadata, updated_at
        }}

        :returning
    """

    queries = [
        verify_developer_id_query(developer_id),
        verify_developer_owns_resource_query(
            developer_id, "sessions", session_id=session_id
        ),
        update_query,
    ]

    return (
        queries,
        {
            "session_update_vals": session_update_vals,
            "session_id": str(session_id),
            "developer_id": str(developer_id),
            "metadata": metadata,
        },
    )
