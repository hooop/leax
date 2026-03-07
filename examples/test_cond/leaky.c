#include <stdlib.h>
#include <string.h>


char	*create_buffer(int size)
{
	char	*buf;

	buf = malloc(size);
	strcpy(buf, "hello");
	return (buf);
}

void	process(int should_free)
{
	char	*data;

	data = create_buffer(64);
	if (should_free)
	{
		free(data);
	}
}

int	main(void)
{
	process(0);
	return (0);
}
