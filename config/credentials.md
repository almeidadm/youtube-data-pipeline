# Credenciais e variaveis de ambiente

## API Key (publico)
- Defina `YOUTUBE_API_KEY` no ambiente.
- Restrinja a chave por API (YouTube Data API v3) e por origem quando aplicavel.

## OAuth 2.0 (opcional)
- Preencha `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` e `YOUTUBE_REFRESH_TOKEN`.
- Use OAuth apenas quando precisar de dados privados ou operacoes de escrita.
- Se voce ja tiver um access token valido, defina `YOUTUBE_ACCESS_TOKEN` para chamadas autenticadas.

## Exemplo
Copie `.env.example` e preencha as variaveis.
