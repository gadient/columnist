from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status

from . import audit_log
from .config import settings
from .db import get_connection
from .tenancy import current_user
from .schemas import (
    BoardSnapshotInput,
    BoardView,
    CreateBoardInput,
    CreateCardInput,
    CreateColumnInput,
    CreateMemberInput,
    CreateWorkspaceInput,
    MoveCardInput,
    RenameWorkspaceInput,
    ReorderCardsInput,
    ReorderColumnsInput,
    ToggleCardInput,
    UpdateBoardInput,
    UpdateCardInput,
    UpdateColumnInput,
    UpdateMemberInput,
    WorkspaceView,
)
from .store import (
    accessible_board_ids,
    accessible_workspace_ids,
    assign_member_to_card,
    create_board,
    create_card,
    create_column,
    create_member,
    create_workspace,
    delete_board,
    delete_card,
    delete_column,
    delete_member,
    delete_workspace,
    get_board,
    list_boards,
    list_workspace_boards,
    list_workspaces,
    move_card,
    rename_workspace,
    reorder_cards,
    reorder_columns,
    sync_board_snapshot,
    toggle_card,
    unassign_member_from_card,
    update_board,
    update_card,
    update_column,
    update_member,
    user_can_access_workspace,
)


def _scoped(user: Optional[dict[str, Any]]) -> bool:
    """True when tenant scoping applies (Cognito on + an authenticated caller)."""
    return bool(settings.cognito_enabled and user)

workspace_router = APIRouter(prefix="/workspaces", tags=["workspaces"])
router = APIRouter(prefix="/boards", tags=["boards"])


@workspace_router.get("", response_model=list[WorkspaceView], summary="List workspaces")
def list_workspaces_endpoint(user: Optional[dict[str, Any]] = Depends(current_user)) -> list[WorkspaceView]:
    conn = get_connection()
    try:
        accessible = accessible_workspace_ids(conn, user["id"]) if _scoped(user) else None
        return list_workspaces(conn, accessible)
    finally:
        conn.close()


@workspace_router.post("", response_model=WorkspaceView, status_code=status.HTTP_201_CREATED, summary="Create workspace")
def create_workspace_endpoint(
    payload: CreateWorkspaceInput,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> WorkspaceView:
    conn = get_connection()
    try:
        return create_workspace(conn, payload.name, owner=user if _scoped(user) else None)
    finally:
        conn.close()


@workspace_router.patch("/{workspace_id}", response_model=WorkspaceView, summary="Rename workspace")
def rename_workspace_endpoint(workspace_id: str, payload: RenameWorkspaceInput) -> WorkspaceView:
    conn = get_connection()
    try:
        return rename_workspace(conn, workspace_id, payload.name)
    finally:
        conn.close()


@workspace_router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete workspace")
def delete_workspace_endpoint(workspace_id: str) -> Response:
    conn = get_connection()
    try:
        delete_workspace(conn, workspace_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        conn.close()


@workspace_router.get("/{workspace_id}/boards", response_model=list[BoardView], summary="List boards in workspace")
def list_workspace_boards_endpoint(workspace_id: str) -> list[BoardView]:
    conn = get_connection()
    try:
        return list_workspace_boards(conn, workspace_id)
    finally:
        conn.close()


@router.get("", response_model=list[BoardView], summary="List boards")
def list_boards_endpoint(user: Optional[dict[str, Any]] = Depends(current_user)) -> list[BoardView]:
    conn = get_connection()
    try:
        accessible = accessible_board_ids(conn, user["id"]) if _scoped(user) else None
        return list_boards(conn, accessible)
    finally:
        conn.close()


@router.post("", response_model=BoardView, status_code=status.HTTP_201_CREATED, summary="Create board")
def create_board_endpoint(
    payload: CreateBoardInput,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> BoardView:
    conn = get_connection()
    try:
        # A board must be created inside a workspace the caller can access.
        if _scoped(user):
            if not payload.workspaceId or not user_can_access_workspace(conn, user["id"], payload.workspaceId):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        return create_board(
            conn,
            title=payload.title,
            description=payload.description,
            columns=[column.model_dump() for column in payload.columns],
            team_members=[member.model_dump() for member in payload.teamMembers],
            workspace_id=payload.workspaceId,
        )
    finally:
        conn.close()


@router.get("/{board_id}", response_model=BoardView, summary="Get board")
def get_board_endpoint(board_id: str) -> BoardView:
    conn = get_connection()
    try:
        return get_board(conn, board_id)
    finally:
        conn.close()


@router.patch("/{board_id}", response_model=BoardView, summary="Update board")
def update_board_endpoint(board_id: str, payload: UpdateBoardInput) -> BoardView:
    conn = get_connection()
    try:
        return update_board(conn, board_id, payload.title, payload.description)
    finally:
        conn.close()


@router.put("/{board_id}/snapshot", response_model=BoardView, summary="Sync full board snapshot")
def sync_board_snapshot_endpoint(
    board_id: str,
    payload: BoardSnapshotInput,
    user: Optional[dict[str, Any]] = Depends(current_user),
) -> BoardView:
    conn = get_connection()
    try:
        result = sync_board_snapshot(conn, board_id, payload.model_dump())
        if _scoped(user):  # board-level, per-user audit — only meaningful with auth on
            audit_log.record("board.save", actor=user, instance_id=user.get("instance_id"), target=board_id)
        return result
    finally:
        conn.close()


@router.delete("/{board_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete board")
def delete_board_endpoint(board_id: str) -> Response:
    conn = get_connection()
    try:
        delete_board(conn, board_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        conn.close()


@router.post("/{board_id}/columns", response_model=BoardView, summary="Create column")
def create_column_endpoint(board_id: str, payload: CreateColumnInput) -> BoardView:
    conn = get_connection()
    try:
        return create_column(conn, board_id, payload.title, payload.position)
    finally:
        conn.close()


@router.patch("/{board_id}/columns/{column_id}", response_model=BoardView, summary="Rename column")
def update_column_endpoint(board_id: str, column_id: str, payload: UpdateColumnInput) -> BoardView:
    conn = get_connection()
    try:
        return update_column(conn, board_id, column_id, payload.title)
    finally:
        conn.close()


@router.delete("/{board_id}/columns/{column_id}", response_model=BoardView, summary="Delete column")
def delete_column_endpoint(board_id: str, column_id: str) -> BoardView:
    conn = get_connection()
    try:
        return delete_column(conn, board_id, column_id)
    finally:
        conn.close()


@router.put("/{board_id}/columns/reorder", response_model=BoardView, summary="Reorder columns")
def reorder_columns_endpoint(board_id: str, payload: ReorderColumnsInput) -> BoardView:
    conn = get_connection()
    try:
        return reorder_columns(conn, board_id, payload.columnIds)
    finally:
        conn.close()


@router.post("/{board_id}/cards", response_model=BoardView, summary="Create card")
def create_card_endpoint(board_id: str, payload: CreateCardInput) -> BoardView:
    conn = get_connection()
    try:
        return create_card(
            conn,
            board_id,
            payload.columnId,
            payload.title,
            payload.description,
            payload.priority,
            payload.dueDate,
            payload.assignees,
        )
    finally:
        conn.close()


@router.patch("/{board_id}/cards/{card_id}", response_model=BoardView, summary="Update card")
def update_card_endpoint(board_id: str, card_id: str, payload: UpdateCardInput) -> BoardView:
    conn = get_connection()
    try:
        return update_card(
            conn,
            board_id,
            card_id,
            payload.title,
            payload.description,
            payload.priority,
            payload.dueDate,
            payload.completed,
        )
    finally:
        conn.close()


@router.delete("/{board_id}/cards/{card_id}", response_model=BoardView, summary="Delete card")
def delete_card_endpoint(board_id: str, card_id: str) -> BoardView:
    conn = get_connection()
    try:
        return delete_card(conn, board_id, card_id)
    finally:
        conn.close()


@router.post("/{board_id}/cards/{card_id}/move", response_model=BoardView, summary="Move card")
def move_card_endpoint(board_id: str, card_id: str, payload: MoveCardInput) -> BoardView:
    conn = get_connection()
    try:
        return move_card(conn, board_id, card_id, payload.targetColumnId, payload.targetPosition)
    finally:
        conn.close()


@router.put(
    "/{board_id}/columns/{column_id}/cards/reorder",
    response_model=BoardView,
    summary="Reorder cards in column",
)
def reorder_cards_endpoint(board_id: str, column_id: str, payload: ReorderCardsInput) -> BoardView:
    conn = get_connection()
    try:
        return reorder_cards(conn, board_id, column_id, payload.cardIds)
    finally:
        conn.close()


@router.post("/{board_id}/cards/{card_id}/toggle-complete", response_model=BoardView, summary="Toggle card completion")
def toggle_card_endpoint(board_id: str, card_id: str, payload: ToggleCardInput) -> BoardView:
    conn = get_connection()
    try:
        return toggle_card(conn, board_id, card_id, payload.completed)
    finally:
        conn.close()


@router.post("/{board_id}/members", response_model=BoardView, summary="Create member")
def create_member_endpoint(board_id: str, payload: CreateMemberInput) -> BoardView:
    conn = get_connection()
    try:
        return create_member(conn, board_id, payload.name, payload.initials, payload.color)
    finally:
        conn.close()


@router.patch("/{board_id}/members/{member_id}", response_model=BoardView, summary="Update member")
def update_member_endpoint(board_id: str, member_id: str, payload: UpdateMemberInput) -> BoardView:
    conn = get_connection()
    try:
        return update_member(conn, board_id, member_id, payload.name, payload.initials, payload.color)
    finally:
        conn.close()


@router.delete("/{board_id}/members/{member_id}", response_model=BoardView, summary="Delete member")
def delete_member_endpoint(board_id: str, member_id: str) -> BoardView:
    conn = get_connection()
    try:
        return delete_member(conn, board_id, member_id)
    finally:
        conn.close()


@router.post(
    "/{board_id}/cards/{card_id}/assignees/{member_id}",
    response_model=BoardView,
    summary="Assign member to card",
)
def assign_member_endpoint(board_id: str, card_id: str, member_id: str) -> BoardView:
    conn = get_connection()
    try:
        return assign_member_to_card(conn, board_id, card_id, member_id)
    finally:
        conn.close()


@router.delete(
    "/{board_id}/cards/{card_id}/assignees/{member_id}",
    response_model=BoardView,
    summary="Unassign member from card",
)
def unassign_member_endpoint(board_id: str, card_id: str, member_id: str) -> BoardView:
    conn = get_connection()
    try:
        return unassign_member_from_card(conn, board_id, card_id, member_id)
    finally:
        conn.close()
