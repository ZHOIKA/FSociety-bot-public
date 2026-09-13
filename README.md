# F SOCIETY

Versão pública e sanitizada do bot **F SOCIETY**, desenvolvido em Python com `discord.py`.

Esta edição é mantida separada do repositório privado de produção e possui um histórico Git novo. Arquivos de ambiente, bancos locais, logs, crashes, backups e credenciais não fazem parte deste repositório.

## Recursos desta edição

- Central visual de configuração
- Tickets privados com histórico
- Moderação e proteção contra spam/convites
- Boas-vindas e cargo automático
- XP, níveis e economia básica
- Alertas de CVE usando a NVD/NIST
- Mensagens programadas
- DarkWeb Watch para CTI baseada em metadados de fontes configuradas

Os módulos internos e experimentais usados apenas na instalação privada não são publicados automaticamente aqui.

## Requisitos

- Python 3.13+
- Bot criado no Discord Developer Portal

## Instalação

```bash
git clone https://github.com/ZHOIKA/FSociety-bot-public.git
cd FSociety-bot-public
python3 -m pip install -r requirements.txt
cp .env.example .env
```

No mínimo, configure o token do seu próprio bot:

```env
FSOCIETY_TOKEN=
FSOCIETY_PREFIX=!
```

Depois execute:

```bash
python3 start.py
```

O arquivo `fsociety.db` é criado localmente em tempo de execução e fica fora do Git.

## DarkWeb Watch

O módulo trabalha com metadados de fontes configuradas pelo administrador. Para consultar endereços `.onion`, o operador precisa configurar seu próprio proxy Tor com `FSOCIETY_ONION_PROXY`. Para proxy SOCKS, instale também `aiohttp-socks`.

## Segurança

Nunca publique `.env`, tokens do Discord, chaves de API, Personal Access Tokens, bancos SQLite de produção, logs, crash dumps ou backups. O `.gitignore` desta edição bloqueia esses artefatos por padrão.

Se uma credencial real for publicada acidentalmente, apagar o arquivo não basta: revogue ou rotacione a credencial no serviço correspondente.

## Edição pública x produção

Este repositório é uma snapshot pública deliberadamente separada. O repositório privado continua sendo o ambiente de produção e pode possuir módulos/configurações adicionais que não pertencem à distribuição pública.

## Licença

GPL-3.0-or-later.
