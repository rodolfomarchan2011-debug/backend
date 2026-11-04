-- =====================================================================
-- PILOTO: tabela de preco do clube dentro do SIM
-- Data: 04/11/2026
--
-- O que faz: cria a tabela "CLUBE TESTE" como copia da tabela normal
-- ("PREÇO VISTA", 896 produtos) e muda SO o preco da PICANHA KG para
-- R$ 10,00. Serve para provar que, ao escolher o cliente na venda, o SIM
-- aplica a tabela de preco dele automaticamente.
--
-- NAO mexe na tabela normal, nao mexe em venda, nao mexe em produto.
-- Desfazer: rode a secao (B) no fim do arquivo (apaga a tabela e os precos
-- dela em cascata).
--
-- COMO RODAR: pgAdmin > banco SIM > Query Tool > colar > F5.
-- (precisa de um usuario com permissao de escrita em TabelaPreco e
--  ProdutoTabelaPreco; o usuario do clube NAO tem essa permissao)
-- =====================================================================

BEGIN;

-- (A1) cria a tabela de preco de teste, se ainda nao existir
INSERT INTO "TabelaPreco" ("Id", "NomeTabela", "LetraAtalho", "TodosProdutos", "FrenteCaixa")
SELECT 'c1b0e000-0000-4000-8000-000000000001'::uuid, 'CLUBE TESTE', 'C', true, true
WHERE NOT EXISTS (SELECT 1 FROM "TabelaPreco" WHERE "NomeTabela" = 'CLUBE TESTE');

-- (A2) copia o preco de TODOS os produtos da tabela normal para a nova
--      (tabela incompleta poderia fazer o caixa puxar preco zerado)
INSERT INTO "ProdutoTabelaPreco" ("ProdutoId", "TabelaPrecoId", "Lucro", "PrecoVenda")
SELECT p."ProdutoId", clube."Id", p."Lucro", p."PrecoVenda"
FROM "ProdutoTabelaPreco" p
CROSS JOIN (SELECT "Id" FROM "TabelaPreco" WHERE "NomeTabela" = 'CLUBE TESTE') AS clube
WHERE p."TabelaPrecoId" = (
        SELECT "Id" FROM "TabelaPreco"
        WHERE "NomeTabela" <> 'CLUBE TESTE'
        ORDER BY "NomeTabela" LIMIT 1
      )
ON CONFLICT ("ProdutoId", "TabelaPrecoId") DO NOTHING;

-- (A3) preco de teste so na picanha
UPDATE "ProdutoTabelaPreco"
SET "PrecoVenda" = 10.00
WHERE "TabelaPrecoId" = (SELECT "Id" FROM "TabelaPreco" WHERE "NomeTabela" = 'CLUBE TESTE')
  AND "ProdutoId" = (SELECT "Id" FROM "Produto" WHERE "NomeProduto" = 'PICANHA KG');

COMMIT;


-- =====================================================================
-- (B) DESFAZER o piloto (rode depois do teste)
-- =====================================================================
-- BEGIN;
-- -- devolve quem estiver apontando para a tabela de teste
-- UPDATE "Cliente" SET "TabelaPrecoId" = NULL
--  WHERE "TabelaPrecoId" = (SELECT "Id" FROM "TabelaPreco" WHERE "NomeTabela" = 'CLUBE TESTE');
-- -- apaga a tabela; os precos dela caem junto (FK ON DELETE CASCADE)
-- DELETE FROM "TabelaPreco" WHERE "NomeTabela" = 'CLUBE TESTE';
-- COMMIT;


-- =====================================================================
-- (C) CONFERIR
-- =====================================================================
-- SELECT t."NomeTabela", count(ptp."ProdutoId") AS produtos
--   FROM "TabelaPreco" t LEFT JOIN "ProdutoTabelaPreco" ptp ON ptp."TabelaPrecoId" = t."Id"
--  GROUP BY t."NomeTabela";
--
-- SELECT ptp."PrecoVenda"
--   FROM "ProdutoTabelaPreco" ptp
--   JOIN "TabelaPreco" t ON t."Id" = ptp."TabelaPrecoId"
--  WHERE t."NomeTabela" = 'CLUBE TESTE'
--    AND ptp."ProdutoId" = (SELECT "Id" FROM "Produto" WHERE "NomeProduto" = 'PICANHA KG');
