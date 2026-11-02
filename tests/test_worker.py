from datasifter.directory.worker import DirectoryWorker


def test_worker_reports_cancellation_before_operation_starts():
    worker = DirectoryWorker(lambda *_args: "unexpected")
    failures = []
    worker.signals.failed.connect(failures.append)
    worker.cancel()

    worker.run()

    assert failures == ["Operation cancelled before it started."]


def test_worker_reports_cancellation_after_operation_returns():
    def operation(cancelled, _progress):
        cancelled.set()
        return "completed"

    worker = DirectoryWorker(operation)
    failures = []
    successes = []
    worker.signals.failed.connect(failures.append)
    worker.signals.succeeded.connect(successes.append)

    worker.run()

    assert not successes
    assert len(failures) == 1
    assert "may already have completed" in failures[0]


def test_worker_reports_operation_failure_when_cancellation_was_requested():
    def operation(cancelled, _progress):
        cancelled.set()
        raise RuntimeError("cancelled during LDAP request")

    worker = DirectoryWorker(operation)
    failures = []
    worker.signals.failed.connect(failures.append)

    worker.run()

    assert failures == ["cancelled during LDAP request"]
