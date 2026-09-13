#!/usr/bin/env python3
"""Inicializador enxuto da edição pública do F SOCIETY.

A edição pública não inclui banco, logs, backups ou bootstrap de produção.
O banco SQLite é criado localmente pelo próprio bot em tempo de execução.
"""
from bot import main


if __name__ == '__main__':
    main()
