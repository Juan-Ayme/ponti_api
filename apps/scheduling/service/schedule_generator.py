from apps.academic_setup.models import PeriodoAcademico, Materias, EspaciosFisicos
from apps.users.models import Docentes
from apps.scheduling.models import Grupos, DisponibilidadDocentes, HorariosAsignados, ConfiguracionRestricciones, BloquesHorariosDefinicion
from .conflict_validator import ConflictValidatorService # Importar el validador

class ScheduleGeneratorService:
    def __init__(self, periodo: PeriodoAcademico):
        self.periodo = periodo
        self.validator = ConflictValidatorService(periodo=self.periodo)
        self.unresolved_conflicts = []
        self.generation_stats = {"asignaciones_exitosas": 0, "intentos_fallidos": 0}

    def _get_data_needed(self):
        """Recopila todos los datos necesarios para la generación."""
        self.grupos_a_programar = Grupos.objects.filter(periodo=self.periodo).select_related('materia', 'carrera')
        self.docentes_disponibles = Docentes.objects.prefetch_related(
            'especialidades',
            'disponibilidades__bloque_horario' # Para acceder a la disponibilidad del docente
        ).all() # Podrías filtrar por unidad académica si es relevante
        self.espacios_disponibles = EspaciosFisicos.objects.select_related('tipo_espacio').all() # Filtrar por unidad si es necesario
        self.bloques_horarios = BloquesHorariosDefinicion.objects.filter(
            # Filtra por días laborables configurados, ej. L-V o L-S (RU13)
        ).order_by('dia_semana', 'hora_inicio')
        self.restricciones_configuradas = ConfiguracionRestricciones.objects.filter(
            (models.Q(periodo_aplicable=self.periodo) | models.Q(periodo_aplicable__isnull=True)),
            esta_activa=True
        )
        # Cargar disponibilidad de docentes para el periodo
        self.docente_disponibilidad_map = self._map_docente_disponibilidad()
        # ... y otros datos que necesites pre-procesar

    def _map_docente_disponibilidad(self):
        """Crea un mapa de fácil acceso para la disponibilidad de docentes."""
        disponibilidades = DisponibilidadDocentes.objects.filter(periodo=self.periodo, esta_disponible=True) \
            .select_related('docente', 'bloque_horario')

        dispo_map = {} # { (docente_id, dia_semana, bloque_id): preferencia }
        for d in disponibilidades:
            key = (d.docente_id, d.dia_semana, d.bloque_horario_id)
            dispo_map[key] = d.preferencia
        return dispo_map

    def _es_docente_disponible(self, docente_id, dia_semana, bloque_id):
        return (docente_id, dia_semana, bloque_id) in self.docente_disponibilidad_map

    def _cumple_restricciones_docente(self, docente, dia_semana, bloque_horario, materia):
        # Verificar especialidad del docente vs materia (RU05, RD16)
        # Verificar carga horaria máxima (RD23, RG02)
        # Verificar otras restricciones específicas del docente (RD03)
        # ...
        return True

    def _cumple_restricciones_espacio(self, espacio, materia, grupo):
        # Verificar tipo de espacio requerido por la materia (RU08, RG03, RD07)
        # Verificar capacidad del espacio vs tamaño del grupo
        # ...
        return True

    def generar_horarios_automaticos(self):
        # AQ02: El sistema debe permitir la generación de horarios ... en un tiempo no mayor a 10 minutos.
        # RS08: El sistema debe implementar algoritmos de optimización...

        self._get_data_needed()
        HorariosAsignados.objects.filter(periodo=self.periodo).delete() # Limpiar horarios previos del periodo (o marcarlos como obsoletos)
        self.unresolved_conflicts = []
        self.generation_stats = {"asignaciones_exitosas": 0, "intentos_fallidos": 0, "grupos_programados": 0, "grupos_no_programados": 0}


        # Lógica del Algoritmo (Ejemplo muy simplificado - ESTO ES LO COMPLEJO):
        # 1. Ordenar grupos (por prioridad, dificultad, etc.)
        # 2. Para cada grupo y sus sesiones requeridas (basadas en horas de materia):
        #    a. Encontrar posibles docentes (especialidad, disponibilidad).
        #    b. Encontrar posibles espacios (tipo, capacidad, disponibilidad).
        #    c. Encontrar posibles bloques horarios (disponibilidad de los anteriores).
        #    d. Aplicar un sistema de puntuación o heurística para elegir la mejor combinación.
        #    e. Intentar asignar y validar conflictos con self.validator.check_slot_conflict(...)
        #    f. Si se asigna, marcar recursos como usados para ese slot y actualizar estadísticas.
        #    g. Si no se puede asignar, registrar el conflicto/problema.

        for grupo in self.grupos_a_programar:
            materia = grupo.materia
            horas_necesarias = materia.horas_totales # Asumimos que cada bloque cubre 1 hora, simplificación
            asignaciones_hechas_para_grupo = 0

            # Determinar docentes candidatos para esta materia/grupo
            docentes_candidatos = [
                d for d in self.docentes_disponibles
                if any(e in d.especialidades.all() for e in materia.materiaespecialidadesrequeridas_set.all().values_list('especialidad', flat=True))
            ]
            if grupo.docente_asignado_directamente: # Priorizar docente pre-asignado
                docentes_candidatos.insert(0, grupo.docente_asignado_directamente)


            # Iterar hasta cubrir las horas necesarias o no encontrar más slots
            for _ in range(int(horas_necesarias)): # Simplificación: cada iteración es 1 hora/bloque
                if asignaciones_hechas_para_grupo >= horas_necesarias:
                    break

                mejor_opcion = None
                mejor_score = -float('inf')

                for docente_cand in docentes_candidatos:
                    for espacio_cand in self.espacios_disponibles:
                        if not self._cumple_restricciones_espacio(espacio_cand, materia, grupo):
                            continue
                        for bloque_cand in self.bloques_horarios:
                            # Verificar si el slot ya está ocupado o si hay conflicto
                            if self.validator.check_slot_conflict(
                                    docente_id=docente_cand.docente_id,
                                    espacio_id=espacio_cand.espacio_id,
                                    grupo_id=grupo.grupo_id, # Para evitar que el mismo grupo se programe dos veces en el mismo slot
                                    dia_semana=bloque_cand.dia_semana,
                                    bloque_id=bloque_cand.bloque_def_id
                            ):
                                continue

                            if not self._es_docente_disponible(docente_cand.docente_id, bloque_cand.dia_semana, bloque_cand.bloque_def_id):
                                continue

                            if not self._cumple_restricciones_docente(docente_cand, bloque_cand.dia_semana, bloque_cand, materia):
                                continue

                            # Aquí podrías añadir un score basado en preferencias, etc.
                            score_actual = self.docente_disponibilidad_map.get((docente_cand.docente_id, bloque_cand.dia_semana, bloque_cand.bloque_def_id), 0)

                            if score_actual > mejor_score:
                                mejor_score = score_actual
                                mejor_opcion = {
                                    "grupo": grupo, "docente": docente_cand, "espacio": espacio_cand,
                                    "dia_semana": bloque_cand.dia_semana, "bloque_horario": bloque_cand
                                }

                if mejor_opcion:
                    HorariosAsignados.objects.create(
                        grupo=mejor_opcion["grupo"],
                        docente=mejor_opcion["docente"],
                        espacio=mejor_opcion["espacio"],
                        periodo=self.periodo,
                        dia_semana=mejor_opcion["dia_semana"],
                        bloque_horario=mejor_opcion["bloque_horario"]
                    )
                    asignaciones_hechas_para_grupo += 1 # O las horas que cubre el bloque
                    self.generation_stats["asignaciones_exitosas"] += 1
                    # Marcar el slot como usado para el validador en esta sesión de generación
                    self.validator.mark_slot_used(
                        docente_id=mejor_opcion["docente"].docente_id,
                        espacio_id=mejor_opcion["espacio"].espacio_id,
                        grupo_id=mejor_opcion["grupo"].grupo_id,
                        dia_semana=mejor_opcion["dia_semana"],
                        bloque_id=mejor_opcion["bloque_horario"].bloque_def_id
                    )
                else:
                    self.unresolved_conflicts.append(f"No se pudo asignar una sesión para el grupo {grupo.codigo_grupo} (materia: {materia.nombre_materia}).")
                    self.generation_stats["intentos_fallidos"] += 1 # Para esta sesión/hora del grupo
                    break # No se pudo asignar una hora para este grupo, pasar al siguiente o registrar


            if asignaciones_hechas_para_grupo >= horas_necesarias:
                self.generation_stats["grupos_programados"] += 1
            else:
                self.generation_stats["grupos_no_programados"] += 1


        # Limpiar el estado del validador para la próxima vez
        self.validator.clear_session_assignments()

        return {
            "stats": self.generation_stats,
            "unresolved_conflicts": self.unresolved_conflicts
        }
