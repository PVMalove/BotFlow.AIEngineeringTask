"""Who may do what with a task."""

from kernel_platform.security.actor import Actor, ActorRole

from domain.entities.task import Task


def may_change_status(actor: Actor, task: Task) -> bool:
    """The author and any admin may move a task between all three statuses."""
    return actor.role is ActorRole.ADMIN or actor.id == task.author_id
