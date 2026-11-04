-- 04/11/2026 - Liga cada oferta do clube a um produto do SIM.
-- Sem esse vinculo o sync nao sabe qual preco mudar na tabela do clube.
-- (ALTER TABLE ADD COLUMN nao tem IF NOT EXISTS no TiDB: rode uma vez.)

ALTER TABLE ofertas
  ADD COLUMN sim_produto_id   VARCHAR(36)  NULL,
  ADD COLUMN sim_produto_nome VARCHAR(160) NULL;
