{#
  Override del generador de nombres de schema.

  Comportamiento default de dbt: concatena <target.schema>_<custom_schema>
  Ej: target.schema='staging' + +schema='marts' -> 'staging_marts'

  Lo que queremos: usar el +schema custom tal cual ('marts' -> 'marts').
  Solo concatenar si NO hay custom_schema_name (caer al target.schema).

  Esto es el patron oficial dbt para multi-schema con nombres limpios.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}
        {{ default_schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
