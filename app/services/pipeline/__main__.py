"""包级入口：`python -m app.services.pipeline`。

单独放一个 `__main__.py` 而不是让使用者调 `python -m app.services.pipeline.run`：
后者会因为 `__init__.py` 已经导入过 `run` 而打印 RuntimeWarning
（「found in sys.modules after import of package ... but prior to execution」）。
入口只有这一个写法，看起来也短一些。
"""

from .run import main

if __name__ == "__main__":
    raise SystemExit(main())
