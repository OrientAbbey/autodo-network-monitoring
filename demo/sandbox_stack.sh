#!/usr/bin/env bash
# Démarre MariaDB + source simulée + faux Orange + puits SMTP + application, puis exécute "$@".
# Usage : sandbox_stack.sh <repo_dir> <commande...>
set -u
REPO="$1"; shift
DEMO="$(cd "$(dirname "$0")" && pwd)"
export DEMO_DIR="$DEMO" DEMO_MAIL_DIR=/tmp/autodo_demo_mail
mkdir -p /run/mysqld && chown -R mysql:mysql /run/mysqld
(nohup mariadbd --user=mysql --bind-address=127.0.0.1 --port=3306 >/tmp/mariadbd.log 2>&1 &)
for i in $(seq 1 40); do mariadb -uroot -e "select 1" >/dev/null 2>&1 && break; sleep 1; done
set -a; . "$DEMO/env.demo"; set +a
rm -rf "$DEMO_MAIL_DIR"; rm -f "$REPO/core/orange_token.json" "$REPO/core/email_notifications.json" "$REPO/app_unified.log"
cd "$DEMO"
(nohup python3 -m uvicorn mock_orange_api:app --port 9001 --log-level warning >/tmp/mock_orange.log 2>&1 &)
(nohup python3 mail_sink.py >/tmp/mail_sink.log 2>&1 &)
"$@"; RC=$?
pkill -f "uvicorn mock_orange_api" ; pkill -f mail_sink.py ; pkill -f "seed_demo_data.py follow"; pkill -f "python3 main.py"
mariadb-admin -uroot shutdown >/dev/null 2>&1
exit $RC
