"""repipe — trigger a CI pipeline and auto-retry it on transient failures.

Zero-dependency: Python 3 standard library only. Distributed as a single
executable built with `zipapp` (see build.sh), so `curl … | bash` installs one
file while the source stays organized as a package.

Package map:
  errors      RepipeError + exit-code constants
  model       normalized, provider-neutral domain model (Run/Step/Target/…)
  gitutil     git discovery (remote → host/workspace/repo, branch)
  ymlparse    minimal YAML-subset parser for bitbucket-pipelines.yml
  http        auth + urllib helpers (only providers use these)
  providers/  Provider interface + host-keyed registry + Bitbucket adapter
  output      small formatting helpers
  cli         argparse wiring + command handlers + main()
"""

__version__ = "1.6.0"
