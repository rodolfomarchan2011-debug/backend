-- 04/11/2026 - Economia real do cliente do clube.
-- Com a tabela de preco do cliente, o SIM NAO grava desconto na venda (ele
-- grava um preco menor). Entao a economia passa a ser calculada pelo sync:
-- soma de (preco normal - preco pago) x quantidade, item por item.
-- total_desconto continua sendo o desconto que o caixa deu na mao.

ALTER TABLE compras
  ADD COLUMN economia_clube DECIMAL(12,2) NOT NULL DEFAULT 0;
