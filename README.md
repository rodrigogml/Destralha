# Destralha

Aplicativo para Windows que identifica caches e arquivos candidatos à limpeza,
mostra seus efeitos e permite revisar a seleção antes de excluir. Desenvolvido
em Python/Tkinter, com identidade visual própria em verde-petróleo e turquesa.

## Executar

Requer Python com Tkinter e a dependência registrada em `requirements.txt`.
Na pasta do projeto:

```powershell
python -m pip install -r requirements.txt
python destralha.py
```

Também é possível abrir **Abrir Destralha.pyw**, sem terminal. No computador
original, o projeto está em `C:\X\Destralha` e possui o atalho **Destralha.lnk**.
Os diretórios analisados pertencem ao usuário que executar o aplicativo; não há
nome de usuário fixo no código.

## Uso

1. **Analisar diretórios** atualiza Caches, Downloads e Outros.
2. Use busca e filtros com contagens combinadas e consulte **Detalhes**.
3. Marque os alvos desejados e use **Simular** ou **Revisar limpeza**.
4. Revise caminhos, efeitos e itens fora dos filtros antes de confirmar.

> [!IMPORTANT]
> A exclusão confirmada é permanente, sem Lixeira. Análise e simulação não
> excluem arquivos. Processos não são encerrados. Fontes de reparo registradas,
> caminhos desconhecidos, links e arquivos em uso continuam protegidos.

Relatórios ficam apenas na memória da sessão. **Salvar relatório** é a única
ação que os grava. Preferências e atalhos locais não são versionados.

## Gerenciamento por instalação

**Programas e sistema** detecta instâncias locais de MySQL e oferece diagnóstico
do Windows. Mostra caminhos, totais, tipos de arquivo e sugestões, sem considerar
todo espaço ocupado como lixo. Tabelas e arquivos internos são preservados.

O MySQL pode ser consultado por conexão local, com senha somente em memória.
O expurgo de binlogs é feito pelo próprio servidor, com verificação da instância,
revisão de backups/replicação e confirmação final. No Windows, análise e limpeza
de componentes usam DISM; não há `/ResetBase` ou exclusão manual de componentes.

[Uso e contratos de segurança da área de gerenciamento](docs/maintenance.md).

## Documentação e testes

[Documentação de uso, segurança e manutenção](docs/index.md).

```powershell
python -m unittest discover -s tests -v
```

Os testes de exclusão usam arquivos artificiais em diretórios temporários.
O catálogo possui checksum; alterações exigem revisão das regras e atualização
consciente do checksum correspondente em `engine.py`.
