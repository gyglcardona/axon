# Casos de prueba reales

Cada subcarpeta es el NIT de una empresa. Dentro, cada caso raro de XML ya resuelto
correctamente se guarda como par de archivos:

```
tests/casos-reales/<nit-empresa>/
  <descripcion-corta>.xml         # el XML real (o una versión anonimizada si tiene datos sensibles)
  <descripcion-corta>.esperado.json   # qué debería producir el sistema al procesarlo
```

Cada vez que se corrige el parser o el motor de reglas, `pytest tests/` corre contra
todos estos casos. Si algo que antes funcionaba deja de funcionar, se entera de
inmediato — este es el mecanismo que evita que una corrección nueva rompa algo
silenciosamente, que fue el problema principal del sistema anterior.

Ejemplos de qué guardar aquí:
- La primera factura donde se vio el IVA en 12 líneas.
- Una factura de KOPPS con Impoconsumo.
- Una factura de la empresa con la política de IVA no discriminado.
