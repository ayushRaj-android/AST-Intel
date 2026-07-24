using System;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Mvc;

namespace TestProject.Controllers
{
    /// <summary>Controller with attributes and DI.</summary>
    [ApiController]
    [Route("/api/jobs")]
    public class JobsController : ControllerBase
    {
        private readonly IJobService _jobService;
        private readonly ILogger<JobsController> _logger;

        public JobsController(IJobService jobService, ILogger<JobsController> logger)
        {
            _jobService = jobService;
            _logger = logger;
        }

        [HttpGet("{id}")]
        [ProducesResponseType(StatusCodes.Status200OK)]
        public async Task<IActionResult> GetJob([FromRoute] Guid id)
        {
            var result = await _jobService.GetAsync(id);
            return Ok(result);
        }

        [HttpPost]
        public async Task<IActionResult> CreateJob([FromBody] JobRequest request)
        {
            _jobService.Process(request.Data);
            return Created();
        }
    }
}
