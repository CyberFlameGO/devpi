from contextlib import closing
from devpi_postgresql import main
from test_devpi_server import conftest
import py
import pytest
import socket
import subprocess
import tempfile
import time


# we need the --backend option here as well
pytest_addoption = conftest.pytest_addoption


def get_open_port(host):
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_port(host, port, timeout=60):
    while timeout > 0:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.settimeout(1)
            if s.connect_ex((host, port)) == 0:
                return
        time.sleep(1)
        timeout -= 1


@pytest.yield_fixture(scope="session")
def postgresql():
    tmpdir = py.path.local(
        tempfile.mkdtemp(prefix='test-', suffix='-devpi-postgresql'))
    try:
        subprocess.check_call(['initdb', tmpdir.strpath])
        with tmpdir.join('postgresql.conf').open('w+b') as f:
            f.write(b"\n".join([
                b"fsync = off",
                b"full_page_writes = off",
                b"synchronous_commit = off"]))
        host = 'localhost'
        port = get_open_port(host)
        p = subprocess.Popen([
            'postgres', '-D', tmpdir.strpath, '-h', host, '-p', str(port)])
        wait_for_port(host, port)
        try:
            subprocess.check_call([
                'createdb', '-h', host, '-p', str(port), 'devpi'])
            settings = dict(host=host, port=port)
            main.Storage(
                tmpdir, notify_on_commit=False,
                cache_size=10000, settings=settings)
            yield settings
        finally:
            p.terminate()
            p.wait()
    finally:
        tmpdir.remove(ignore_errors=True)


class Storage(main.Storage):
    _dbs_created = set()

    @property
    def database(self):
        import hashlib
        db = hashlib.md5(
            self.basedir.strpath.encode('ascii', errors='ignore')).hexdigest()
        if db not in self._dbs_created:
            subprocess.call([
                'createdb', '-h', self.host, '-p', str(self.port),
                '-T', 'devpi', db])
            self._dbs_created.add(db)
        self.__dict__["database"] = db
        return db


@pytest.fixture(autouse=True)
def db_cleanup():
    if len(Storage._dbs_created) < 10:
        return
    for db in set(Storage._dbs_created):
        try:
            subprocess.check_call([
                'dropdb', '-h', Storage.host, '-p', str(Storage.port), db])
        except subprocess.CalledProcessError:
            pass
        else:
            Storage._dbs_created.remove(db)


@pytest.fixture(autouse=True, scope="session")
def devpiserver_storage_backend_mock(postgresql):
    old = main.devpiserver_storage_backend

    def devpiserver_storage_backend(settings=None):
        result = old()
        Storage.host = postgresql['host']
        Storage.port = postgresql['port']
        result['storage'] = Storage
        return result

    main.devpiserver_storage_backend = devpiserver_storage_backend


old_storage_info = conftest.storage_info


@pytest.fixture(scope="session")
def storage_info(request, devpiserver_storage_backend_mock):
    # we need this to trigger the devpiserver_storage_backend_mock fixture
    return old_storage_info(request)


conftest.db_cleanup = db_cleanup
conftest.devpiserver_storage_backend_mock = devpiserver_storage_backend_mock
conftest.postgresql = postgresql
conftest.storage_info = storage_info
