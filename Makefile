.PHONY: \
	clean \
	clean-tests \
	clean-venv \
	format \
	help \
	init \
	test \
	version

## Show this help.
help:
	@awk '/^## .*$$/,/^[~\/\.a-zA-Z_-]+:/' ${MAKEFILE_LIST} | \
	awk '!(NR%2){print $$0p}{p=$$0}' |  \
	awk 'BEGIN {FS = ":.*?##"}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' | \
	sort

## Set up the environment to start coding
init: .git/hooks/pre-commit

## Clean up the environment
clean: clean-tests clean-venv

## Remove all test artifacts
clean-tests:
	rm -rfv .coverage*
	rm -rfv .pytest_cache
	rm -rfv coverage

## Remove the virtualenv so we can start from scratch
clean-venv:
	rm -rf .venv

## Runs the pre-commit tasks against all files, automatically fixing files when it can
format: .git/hooks/pre-commit
	uv tool run pre-commit run --all-files

## Locally run unit tests
test:
	uv run pytest --cov-report=html:coverage/html

## Get the (would-be) version of the current commit (as inferred by setuptools_scm)
version:
	@uv run python -m setuptools_scm


#################################################################################
# Other concrete targets
#################################################################################
.git/hooks/pre-commit: .pre-commit-config.yaml
	uv tool run pre-commit install
