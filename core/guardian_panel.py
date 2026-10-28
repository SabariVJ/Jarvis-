"""Local-only Command Center view. Buttons submit ordinary fresh user commands."""
import json
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                            QPushButton, QTableWidget, QTableWidgetItem, QTextEdit)


def build(parent, rt, submit, colors):
    from core.github_guardian import snapshot, slug
    dialog = QDialog(parent)
    dialog.setWindowTitle('GITHUB COMMAND CENTER — read-only Guardian')
    dialog.setMinimumSize(780, 580)
    dialog.setStyleSheet(f'QDialog {{background:{colors.DARK};}} QLabel,QTextEdit,QTableWidget,QLineEdit '
                        f'{{background:{colors.PANEL};color:{colors.TEXT};}} QPushButton '
                        f'{{background:{colors.PANEL};color:{colors.PRI};padding:6px;border:1px solid {colors.BORDER};}}')
    layout = QVBoxLayout(dialog)
    heading = QLabel('GITHUB COMMAND CENTER')
    heading.setStyleSheet(f'color:{colors.PRI};font:bold 16px "Courier New";padding:6px;')
    layout.addWidget(heading)
    state = QLabel()
    state.setWordWrap(True)
    layout.addWidget(state)
    table = QTableWidget(0, 5)
    table.setObjectName('guardian_repositories')
    table.setHorizontalHeaderLabels(['Repository', 'Monitoring', 'Observed health', 'Failed builds', 'Last sync (epoch)'])
    table.horizontalHeader().setStretchLastSection(True)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    layout.addWidget(table)
    detail = QTextEdit()
    detail.setReadOnly(True)
    detail.setObjectName('guardian_facts')
    layout.addWidget(detail)
    entry = QLineEdit()
    entry.setObjectName('guardian_repository_input')
    entry.setPlaceholderText('Exact owner/repository — selection is explicit; nothing is watched by default')
    layout.addWidget(entry)
    controls = QHBoxLayout()
    layout.addLayout(controls)
    def send(value):
        # No silent model-side authorization or network work on the UI thread.
        submit(value)
        dialog.accept()
    def choose(verb):
        try:
            repository = slug(entry.text().strip())
        except ValueError as exc:
            state.setText(str(exc))
            return
        send(verb + ' github ' + repository)
    for title, callback in [('Discover', lambda: send('discover github repositories')),
            ('Watch selection', lambda: choose('watch')), ('Remove selection', lambda: choose('unwatch')),
            ('Check now', lambda: send('check monitored github projects')),
            ('Pause', lambda: send('pause github monitoring')), ('Resume', lambda: send('resume github monitoring'))]:
        button = QPushButton(title)
        button.clicked.connect(callback)
        controls.addWidget(button)
    foot = QLabel('Local snapshots only. Monitoring requires JARVIS running and the PC awake. '
                  'API reads never publish code. External messaging and coding drafts are not enabled by this panel.')
    foot.setWordWrap(True)
    layout.addWidget(foot)
    def refresh():
        view = snapshot(rt)
        identity = view['identity']
        state.setText('Account: ' + (str(identity.get('login')) + ' (last verified; see timestamp)' if identity.get('connected') else
                      identity.get('error', 'API identity not checked')) + '\nMonitoring: ' +
                      ('PAUSED' if view['settings'].get('paused') else 'allowlist only') +
                      ' | No inference, pushes, releases or deployments from Guardian.')
        rows = sorted(view['watched'].items())
        table.setRowCount(len(rows))
        for index, (repository, watch) in enumerate(rows):
            health = view['health'].get(repository, {})
            for column, value in enumerate((repository, 'selected' if watch.get('enabled') else 'removed',
                    health.get('status', 'not synchronized'), len(health.get('failed_builds', [])), health.get('at', 'never'))):
                table.setItem(index, column, QTableWidgetItem(str(value)))
        detail.setPlainText(json.dumps({'identity': identity, 'health': view['health'], 'read_failures': view['read_failures'], 'events': view['recent_events'],
            'discovered_not_selected': rt.get('guardian', 'discovery', {}),
            'unavailable_features': {'coding': view['coding'], 'external_notifications': view['external_notifications'], 'codex': view['codex']}},
            indent=2, ensure_ascii=False))
    refresh()
    timer = QTimer(dialog)
    timer.setInterval(5000)
    timer.timeout.connect(refresh)
    timer.start()
    return dialog
