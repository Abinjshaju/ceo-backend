import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ceo_backend.models import AgentRun, Conversation, Message


def get_or_create_conversation(
    session: Session, workspace_id: uuid.UUID, kind: str, project_id: uuid.UUID | None = None
) -> Conversation:
    query = select(Conversation).where(
        Conversation.workspace_id == workspace_id, Conversation.kind == kind
    )
    query = query.where(
        Conversation.project_id == project_id if project_id else Conversation.project_id.is_(None)
    )
    conversation = session.scalar(query.order_by(Conversation.created_at).limit(1))
    if conversation is None:
        conversation = Conversation(
            id=uuid.uuid4(), workspace_id=workspace_id, kind=kind, project_id=project_id
        )
        session.add(conversation)
        session.flush()
    return conversation


def project_conversation_ids(session: Session, project_id: uuid.UUID) -> list[uuid.UUID]:
    return list(session.scalars(select(Conversation.id).where(Conversation.project_id == project_id)))


def find_message(session: Session, conversation_id: uuid.UUID, client_message_id: str, role: str) -> Message | None:
    return session.scalar(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.client_message_id == client_message_id,
            Message.role == role,
        )
    )


def add_message(session: Session, conversation_id: uuid.UUID, client_message_id: str, role: str,
                content: str, **fields) -> Message:
    message = Message(
        conversation_id=conversation_id,
        client_message_id=client_message_id,
        role=role,
        content=content,
        **fields,
    )
    try:
        with session.begin_nested():
            session.add(message)
    except IntegrityError:
        existing = find_message(session, conversation_id, client_message_id, role)
        if existing is None:
            raise
        return existing
    return message


def recent_messages(session: Session, conversation_ids: list[uuid.UUID], limit: int = 30,
                    before_id: int | None = None, exclude_ids: set[int] | None = None) -> list[Message]:
    if not conversation_ids:
        return []
    query = select(Message).where(Message.conversation_id.in_(conversation_ids))
    if before_id:
        query = query.where(Message.id < before_id)
    rows = session.scalars(query.order_by(Message.id.desc()).limit(limit + len(exclude_ids or ()))).all()
    rows = [row for row in rows if row.id not in (exclude_ids or set())][:limit]
    return list(reversed(rows))


def run_for_message(session: Session, user_message_id: int) -> AgentRun | None:
    return session.scalar(
        select(AgentRun)
        .where(AgentRun.user_message_id == user_message_id)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )


def message_view(message: Message) -> dict:
    return {
        "id": message.id,
        "client_message_id": message.client_message_id,
        "role": message.role,
        "content": message.content,
        "document_ids": message.document_ids or [],
        "citations": message.citations or [],
        "components": message.components or [],
        "suggested_actions": message.suggested_actions or [],
        "run_id": str(message.run_id) if message.run_id else None,
        "created_at": message.created_at.isoformat(),
    }
