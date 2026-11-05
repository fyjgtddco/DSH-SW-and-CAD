"""Keep Windows descendants inside an unnamed kill-on-close Job from birth."""
import subprocess
import time
from uuid import uuid4

import win32api
import win32con
import win32job
import win32process



class OwnedJob:
    def __init__(self,max_processes=None):
        self.handle = win32job.CreateJobObject(None, "A04-" + uuid4().hex)
        limits = win32job.QueryInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if max_processes is not None:
            if type(max_processes) is not int or not 1<=max_processes<=64:
                self.handle.Close()
                raise ValueError("Process ceiling must be an integer in 1..64")
            limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            limits["BasicLimitInformation"]["ActiveProcessLimit"] = max_processes
        win32job.SetInformationJobObject(self.handle, win32job.JobObjectExtendedLimitInformation, limits)

    def spawn(self, argv, env, cwd):
        process, thread, pid, _ = win32process.CreateProcess(argv[0], subprocess.list2cmdline(argv),
            None, None, False, win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW | win32process.BELOW_NORMAL_PRIORITY_CLASS,
            env, str(cwd), win32process.STARTUPINFO())
        try:
            win32job.AssignProcessToJobObject(self.handle, process)
            identity = self.identity(pid)
            win32process.ResumeThread(thread)
            return identity
        except BaseException:
            win32api.TerminateProcess(process, 90)
            raise
        finally:
            thread.Close()
            process.Close()

    def identity(self, pid):
        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
        try:
            if not win32job.IsProcessInJob(handle, self.handle):
                raise PermissionError("Peer outside owned Job")
            return dict(pid=pid, creation_time=str(win32process.GetProcessTimes(handle)["CreationTime"]),
                        exe=win32process.GetModuleFileNameEx(handle, 0))
        finally:
            handle.Close()

    def active(self):
        return win32job.QueryInformationJobObject(self.handle,
                win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"]

    def stop(self):
        # No persisted PID is ever reopened for termination.
        if self.handle is None:
            return
        try:
            if self.active():
                win32job.TerminateJobObject(self.handle, 91)
            until = time.monotonic() + 2
            while self.active() and time.monotonic() < until:
                time.sleep(.01)
            if self.active():
                raise TimeoutError("Job termination unconfirmed")
        finally:
            self.handle.Close()
            self.handle = None
