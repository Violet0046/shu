"""服务器本地账号管理，无公开注册或账号创建 API。"""
import argparse
import getpass
import secrets
import sys

from .errors import ServiceError
from .services import build_services


def main(argv=None):
    parser = argparse.ArgumentParser(description='服务器阅卷账号管理')
    parser.add_argument('--data-dir', help='与网页及工作进程相同的数据目录')
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('create-user','reset-password'):
        sub = commands.add_parser(command)
        sub.add_argument('username')
        source = sub.add_mutually_exclusive_group()
        source.add_argument('--password-stdin', action='store_true', help='从标准输入读取密码，不放入命令参数')
        source.add_argument('--generate', action='store_true', help='生成随机密码，仅在本次输出')
        if command=='create-user':
            sub.add_argument('--recipient', default='', help='账号分配对象的管理员备注')
    for command in ('disable-user','enable-user'):
        commands.add_parser(command).add_argument('username')
    commands.add_parser('list-users')
    args = parser.parse_args(argv)
    services = build_services(args.data_dir)
    try:
        if args.command=='list-users':
            for user in services.store.all('SELECT username,active,recipient FROM users ORDER BY username'):
                print(f"{user['username']}\t{'启用' if user['active'] else '停用'}\t{user['recipient']}")
            return 0
        if args.command in ('create-user','reset-password'):
            if args.password_stdin:
                password = sys.stdin.readline().rstrip('\r\n')
            elif args.generate:
                password = secrets.token_urlsafe(18)
            else:
                password = getpass.getpass('设置密码（至少 8 位）：')
                if password != getpass.getpass('再次输入密码：'):
                    raise ServiceError(422, '两次密码不一致')
            if args.command=='create-user':
                services.auth.create_account(args.username, password, args.recipient)
            else:
                services.auth.manage_account(args.username, password=password)
            print('账号已创建' if args.command=='create-user' else '密码已重置，旧会话与手机入口已失效')
            if args.generate:
                print('请妥善交给账号使用人，本次生成的密码：'+password)
        else:
            services.auth.manage_account(args.username, active=args.command=='enable-user')
            print('账号状态已更新，旧会话与手机入口已失效')
        return 0
    except ServiceError as exc:
        print(exc.message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
