document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('task-form');
    // Only execute if form exists
    if (form) {
        const progressContainer = document.getElementById('progress-container');
        const taskIdSpan = document.getElementById('task-id');
        const progressBar = document.getElementById('progress-bar');
        const logsUl = document.querySelector('#logs ul');
        const statusBar = document.getElementById('status-bar'); 
        const stopBtn = document.getElementById('stop-btn');     

        let currentTaskId = null;

        form.addEventListener('submit', function (event) {
            event.preventDefault();

            const urls = document.getElementById('urls').value;
            const reportTitle = document.getElementById('report_title').value;
            const useGemini = document.getElementById('use_gemini').checked;

            const formData = {
                urls: urls.split('\n').filter(url => url.trim() !== ''),
                report_title: reportTitle,
                use_gemini: useGemini
            };

            // Reset UI
            progressContainer.style.display = 'block';
            taskIdSpan.textContent = '...';
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            logsUl.innerHTML = '';
            statusBar.textContent = '初始化中...';
            stopBtn.disabled = false;

            fetch('/submit_task', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData),
            })
            .then(response => response.json())
            .then(data => {
                if (data.task_id) {
                    currentTaskId = data.task_id;
                    taskIdSpan.textContent = data.task_id;
                    pollStatus(data.task_id, progressBar, logsUl, statusBar, stopBtn);
                } else {
                    console.error('Failed to start task:', data.error);
                    logsUl.innerHTML = `<li class="list-group-item list-group-item-danger">Failed to start task: ${data.error}</li>`;
                }
            })
            .catch(error => {
                console.error('Error:', error);
                logsUl.innerHTML = `<li class="list-group-item list-group-item-danger">An unexpected error occurred.</li>`;
            });
        });

        // Stop Button Logic
        stopBtn.addEventListener('click', function() {
            if (!currentTaskId) return;
            if (!confirm('確定要停止目前的分析任務嗎？')) return;

            fetch(`/stop_task/${currentTaskId}`, { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        statusBar.textContent = '正在停止任務...';
                        statusBar.className = 'alert alert-warning py-2 mb-3';
                        stopBtn.disabled = true;
                    }
                });
        });
    }

    function pollStatus(taskId, progressBar, logsUl, statusBar, stopBtn) {
        let lastLogText = ''; // Track last log to prevent flickering

        const interval = setInterval(() => {
            fetch(`/task_status/${taskId}`)
                .then(response => response.json())
                .then(data => {
                    // Update progress bar
                    const progress = data.progress || 0;
                    progressBar.style.width = progress + '%';
                    progressBar.textContent = progress + '%';
                    progressBar.setAttribute('aria-valuenow', progress);

                    // Update Status Bar & Logs
                    if (data.log) {
                        statusBar.textContent = data.log; 
                        
                        // Only append if it's a new log message
                        if (data.log !== lastLogText) {
                            lastLogText = data.log;
                            const logItem = document.createElement('li');
                            logItem.className = 'list-group-item';
                            logItem.textContent = data.log;
                            logsUl.appendChild(logItem);
                            
                            // [Fix] Auto scroll to bottom
                            const logsCard = document.querySelector('#logs ul');
                            if(logsCard) logsCard.scrollTop = logsCard.scrollHeight;
                        }
                    } 

                    if (data.status === 'completed') {
                        clearInterval(interval);
                        statusBar.className = 'alert alert-success py-2 mb-3';
                        statusBar.textContent = '任務已完成！';
                        stopBtn.disabled = true;
                        
                        const logItem = document.createElement('li');
                        logItem.className = 'list-group-item list-group-item-success';
                        const downloadUrl = `/download_project/${taskId}`;
                        logItem.innerHTML = `
                            <strong>任務完成!</strong><br>
                            <a href="${downloadUrl}" class="btn btn-success btn-sm mt-2">
                                📥 下載 Word 報告 (.docx)
                            </a>
                        `;
                        logsUl.appendChild(logItem);
                    } else if (data.status === 'cancelled') {
                        clearInterval(interval);
                        statusBar.className = 'alert alert-danger py-2 mb-3';
                        statusBar.textContent = '任務已取消。';
                        stopBtn.disabled = true;
                    } else if (data.status === 'failed') {
                        clearInterval(interval);
                        statusBar.className = 'alert alert-danger py-2 mb-3';
                        statusBar.textContent = '任務發生錯誤。';
                        stopBtn.disabled = true;
                    }
                    
                })
                .catch(error => {
                    console.error('Polling error:', error);
                    clearInterval(interval);
                });
        }, 2000); 
    }
});
