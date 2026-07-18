.PHONY: test test-all test-cov coverage test-chat test-quick

test:
	. venv/bin/activate && python -m pytest tests/ -v --tb=short --ignore=tests/test_chat_full.py

test-all:
	. venv/bin/activate && python -m pytest tests/ -v --tb=short -m "not slow"

test-quick:
	. venv/bin/activate && python -m pytest tests/ --ignore=tests/test_chat_full.py -q --tb=line

test-chat:
	. venv/bin/activate && python -m pytest tests/test_chat_full.py -v --tb=short

test-cov:
	. venv/bin/activate && python -m pytest tests/ --ignore=tests/test_chat_full.py --cov=docker_manager.py --cov=graph_worker.py --cov=worker.py --cov=rq_worker.py --cov=tools --cov-report=term

coverage:
	. venv/bin/activate && python -m pytest tests/ --ignore=tests/test_chat_full.py --cov=docker_manager.py --cov=graph_worker.py --cov=worker.py --cov=rq_worker.py --cov=tools --cov-report=term

install-dev:
	pip install -r requirements.txt -r requirements-test.txt
