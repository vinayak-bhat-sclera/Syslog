# app/utils/constants.py
PRIORITY_MAP = {
    0: "emerg", 1: "alert", 2: "crit", 3: "err",
    4: "warning", 5: "notice", 6: "info", 7: "debug"
}
FACILITY_MAP = {
    0: "kern", 1: "user", 2: "mail", 3: "daemon",
    4: "auth", 5: "syslog", 6: "lpr", 7: "news",
    8: "uucp", 9: "cron", 10: "authpriv", 11: "local0",
    12: "local1", 13: "local2", 14: "local3", 15: "local4",
    16: "local5", 17: "local6", 18: "local7"
}
