# F SOCIETY

Versão pública do bot **F SOCIETY**, desenvolvido em Python com `discord.py`.

Esta edição pública é mantida separada do repositório de produção. Arquivos de ambiente, bancos locais, logs, crashes e credenciais **não fazem parte deste repositório**.

## Recursos

- Central visual de configuração
- Tickets privados
- Moderação
- Boas-vindas
- XP e economia
- Música
- Alertas de CVE
- Mensagens e automações programadas
- Rankings e perfis
- Recursos de comunidade

## Requisitos

- Python 3.13+
- FFmpeg para recursos de áudio
- Bot criado no Discord Developer Portal

## Instalação

```bash
git clone https://github.com/ZHOIKA/FSociety-bot-public.git
cd FSociety-bot-public
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Edite `.env` e informe somente as credenciais necessárias no seu próprio ambiente:

```env
FSOCIETY_TOKEN=
FSOCIETY_PREFIX=!
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_BUCKET=fsociety-backups
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
FSOCIETY_GITHUB_REPO=ZHOIKA/FSociety-bot-public
FSOCIETY_GITHUB_TOKEN=
```

Depois execute:

```bash
python3 start.py
```

## Segurança

Nunca envie para o GitHub:

- `.env`
- tokens do Discord
- chaves do Supabase
- secrets do Spotify
- Personal Access Tokens do GitHub
- bancos `*.db`, `*.sqlite` ou `*.sqlite3`
- logs, crash dumps ou backups de produção

O `.gitignore` desta versão já bloqueia esses artefatos por padrão.

Se uma credencial real for publicada acidentalmente, remova-a do código **e revogue/rotacione a credencial** no serviço correspondente.

## Dados

O banco `fsociety.db` é criado em tempo de execução e não é versionado. Cada instalação deve possuir seus próprios dados.

## Licença

GPL-3.0. Consulte `LICENSE`.
