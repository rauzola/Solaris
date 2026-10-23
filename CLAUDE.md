# Solaris — regras para o Claude

## Git
- **Nunca** adicionar `Co-Authored-By` (nem outra linha de atribuição) em commits ou PRs.
- **Nunca** commitar direto na `main`: cada assunto ganha sua própria branch a partir da `main`
  (ex.: `feat/painel`, `docs/comandos`, `fix/leitura-sono`), e o push/PR é feito a partir dela.
