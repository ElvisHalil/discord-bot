"""
Static alias tables and command vocabularies for the Job, Reminder, Condition & Music Autoplay skill (`src/skills/jobs/catalogs.py`).
"""

from typing import Dict

from .enums import JobManagerAction

JOB_ACTION_ALIASES: Dict[str, str] = {
    "create": JobManagerAction.CREATE,
    "add": JobManagerAction.CREATE,
    "set": JobManagerAction.CREATE,
    "schedule": JobManagerAction.CREATE,
    "remind": JobManagerAction.CREATE,
    "watch": JobManagerAction.CREATE,
    "autoplay": JobManagerAction.AUTOPLAY,
    "radio": JobManagerAction.AUTOPLAY,
    "list": JobManagerAction.LIST,
    "status": JobManagerAction.STATUS,
    "show": JobManagerAction.LIST,
    "active": JobManagerAction.LIST,
    "cancel": JobManagerAction.CANCEL,
    "stop": JobManagerAction.STOP,
    "remove": JobManagerAction.CANCEL,
    "delete": JobManagerAction.CANCEL,
    "unremind": JobManagerAction.CANCEL,
    "clear": JobManagerAction.CLEAR,
    "reset": JobManagerAction.CLEAR,
}

