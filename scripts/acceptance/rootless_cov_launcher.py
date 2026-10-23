"""Source launcher entry point; implementation ships in the Cov adapter wheel.

For selected framework Skills use the registered module invocation:
python -m cov_execution.launcher --config /exact/operator.json -- ...
"""
from cov_execution.launcher import main

if __name__ == '__main__':
    main()
