from test_support.unit_of_work import FakeUnitOfWork


async def test_uncommitted_exit_marks_rollback() -> None:
    uow = FakeUnitOfWork()
    async with uow:
        pass
    assert uow.rolled_back
    assert not uow.committed


async def test_commit_prevents_rollback_on_exit() -> None:
    uow = FakeUnitOfWork()
    async with uow:
        await uow.commit()
    assert uow.committed
    assert not uow.rolled_back
